package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tempfork/acme"
)

// Public, supported LocalAPI operations available in the Android SDK build.
// The cert/ endpoint is NOT available there. No LocalBackend/test hook is used.
type certificateControl interface {
	StatusWithoutPeers(context.Context) (*ipnstate.Status, error)
	SetDNS(context.Context, string, string) error
}

type certificateError string

func (e certificateError) Error() string { return string(e) }

const certificateFailure = "certificate acquisition failed; retained identity preserved"

var acmeToken = regexp.MustCompile(`^[A-Za-z0-9_-]{1,512}$`)

type certificateRetry struct {
	Host          string `json:"host"`
	After         int64  `json:"after"`
	Failure       string `json:"failure"`
	Failures      uint32 `json:"failures,omitempty"`
	AccountSHA256 string `json:"account_sha256,omitempty"`
	KeySHA256     string `json:"key_sha256,omitempty"`
}

// One provider, hostname and network epoch per locked node process. The account
// and leaf keys retain the SDK's existing filenames and are never rotated here.
// Only a fully verified certificate is atomically replaced. A durable attempt
// checkpoint prevents process restart/cancellation from bypassing retry limits.
type acmeCertificates struct {
	parent                    context.Context
	control                   certificateControl
	directory, host, endpoint string
	roots                     *x509.CertPool
	http                      *http.Client
	current                   func() error
	now                       func() time.Time
	gate                      chan struct{}
	mu                        sync.Mutex
	retry                     certificateRetry
	inFlight                  bool
}

func newACMECertificates(parent context.Context, control certificateControl, directory, host, endpoint string, roots *x509.CertPool, client *http.Client, current func() error) (*acmeCertificates, error) {
	if !validDNSName(host) || control == nil || roots == nil || client == nil || client.Transport == nil || current == nil {
		return nil, certificateError("invalid Android certificate provider")
	}
	p := &acmeCertificates{parent: parent, control: control, directory: directory, host: host,
		endpoint: endpoint, roots: roots, http: client, current: current, now: time.Now, gate: make(chan struct{}, 1)}
	p.gate <- struct{}{}
	return p, nil
}

func (p *acmeCertificates) check(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := p.parent.Err(); err != nil {
		return err
	}
	if err := p.current(); err != nil {
		return certificateError("certificate network epoch is unavailable")
	}
	return nil
}

func (p *acmeCertificates) approved(ctx context.Context) error {
	if err := p.check(ctx); err != nil {
		return err
	}
	st, err := p.control.StatusWithoutPeers(ctx)
	if err != nil || st == nil || st.BackendState != "Running" || st.Self == nil || !st.Self.Online || st.CurrentTailnet == nil || !st.CurrentTailnet.MagicDNSEnabled {
		return certificateError("certificate control identity is unavailable")
	}
	host, _, _ := identity(st)
	if host == p.host {
		for _, allowed := range st.CertDomains {
			if strings.TrimSuffix(allowed, ".") == p.host {
				return p.check(ctx)
			}
		}
	}
	return certificateError("certificate hostname is not the current approved node domain")
}

func (p *acmeCertificates) path(suffix string) string {
	return filepath.Join(p.directory, "certs", p.host+suffix)
}

func readCertificateFile(path string) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), "private-certificate-state")
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || !ok || st.Nlink != 1 || st.Uid != uint32(os.Geteuid()) {
		return nil, certificateError("invalid private certificate state file")
	}
	data, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil || len(data) == 0 || len(data) > 1<<20 {
		return nil, certificateError("cannot read private certificate state")
	}
	return data, nil
}

func certificateSigner(data []byte) (crypto.Signer, error) {
	block, rest := pem.Decode(data)
	if block == nil || len(bytes.TrimSpace(rest)) != 0 {
		return nil, certificateError("invalid retained certificate key; refusing replacement")
	}
	var key any
	var err error
	switch block.Type {
	case "EC PRIVATE KEY":
		key, err = x509.ParseECPrivateKey(block.Bytes)
	case "RSA PRIVATE KEY":
		key, err = x509.ParsePKCS1PrivateKey(block.Bytes)
	case "PRIVATE KEY":
		key, err = x509.ParsePKCS8PrivateKey(block.Bytes)
	default:
		err = errors.New("unsupported key encoding")
	}
	if err == nil {
		switch k := key.(type) {
		case *ecdsa.PrivateKey:
			return k, nil
		case *rsa.PrivateKey:
			if k.Validate() == nil {
				return k, nil
			}
		}
	}
	return nil, certificateError("invalid retained certificate key; refusing replacement")
}

func newCertificateKey() ([]byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), nil
}

func (p *acmeCertificates) setRetry(value certificateRetry) error {
	raw, err := json.Marshal(value)
	if err == nil {
		err = writePrivateFile(p.path(".renewal.json"), raw)
	}
	p.mu.Lock()
	p.retry = value
	p.mu.Unlock()
	if err != nil {
		return certificateError("cannot persist certificate retry state")
	}
	return nil
}

func (p *acmeCertificates) RenewalState() (string, int64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.inFlight {
		return "", p.retry.After
	}
	return p.retry.Failure, p.retry.After
}

func certificateRenewalState(client certificateClient) (string, int64) {
	if p, ok := client.(interface{ RenewalState() (string, int64) }); ok {
		return p.RenewalState()
	}
	return "", 0
}

func storeCertificateResult(store *statusStore, client certificateClient, pair *tls.Certificate, err error) error {
	return store.update(func(s *status) {
		s.CertificateReady = err == nil
		s.CertificateRenewalError, s.CertificateRetryAt = certificateRenewalState(client)
		s.Error = ""
		if err != nil {
			s.Error = err.Error()
		} else {
			s.CertificateExpiresAt = pair.Leaf.NotAfter.Unix()
		}
	})
}

// The Android provider checks renewals even without incoming TLS traffic. It
// owns one cancellable worker, joins it before provider/SDK shutdown, and uses
// the same shared verification/status path as a handshake. Termux retains its
// SDK's existing renewal behavior.
func (p *acmeCertificates) startRenewal(store *statusStore, failures chan<- error, interval time.Duration) func() {
	ctx, cancel := context.WithCancel(p.parent)
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				pair, err := fetchPublicCertificate(ctx, p, p.directory, p.host, p.roots)
				if ctx.Err() != nil {
					return
				}
				if err := storeCertificateResult(store, p, pair, err); err != nil {
					select {
					case failures <- certificateError("cannot persist certificate lifecycle status"):
					default:
					}
					return
				}
			}
		}
	}()
	return func() { cancel(); <-done }
}

type certificateMaterial struct {
	account, key, cert []byte
	cached             *tls.Certificate
	renewAt            time.Time
	retry              certificateRetry
}

func certificateFingerprint(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
func (p *acmeCertificates) retained() (*certificateMaterial, error) {
	if err := protectManagedState(p.directory, p.host); err != nil {
		return nil, err
	}
	if err := privateDir(filepath.Join(p.directory, "certs")); err != nil {
		return nil, err
	}
	var account, key, cert []byte
	for _, item := range []struct {
		path string
		data *[]byte
	}{
		{filepath.Join(p.directory, "certs", "acme-account.key.pem"), &account}, {p.path(".key"), &key}, {p.path(".crt"), &cert},
	} {
		data, err := readCertificateFile(item.path)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, certificateError("cannot read retained certificate identity; refusing replacement")
		}
		*item.data = data
	}
	if len(account) == 0 && (len(key) > 0 || len(cert) > 0) || len(key) == 0 && len(cert) > 0 {
		return nil, certificateError("retained certificate identity is incomplete; refusing regeneration")
	}
	for _, data := range [][]byte{account, key} {
		if len(data) > 0 {
			if _, err := certificateSigner(data); err != nil {
				return nil, err
			}
		}
	}
	now := p.now()
	var cached *tls.Certificate
	var renewAt time.Time
	if len(cert) > 0 {
		pair, err := tls.X509KeyPair(cert, key)
		if err != nil || len(pair.Certificate) == 0 {
			return nil, certificateError("retained certificate/key cache is invalid")
		}
		leaf, err := x509.ParseCertificate(pair.Certificate[0])
		if err != nil {
			return nil, certificateError("retained certificate is invalid")
		}
		verifyAt := now
		if !now.Before(leaf.NotAfter) {
			verifyAt = leaf.NotAfter.Add(-time.Second)
		}
		trusted, err := publicCertificateAt(cert, key, p.host, p.roots, verifyAt)
		if err != nil {
			return nil, certificateError("retained certificate fails hostname/public-chain validation")
		}
		if now.Before(leaf.NotAfter) {
			cached = trusted
		}
		renewAt = leaf.NotBefore.Add(leaf.NotAfter.Sub(leaf.NotBefore) * 2 / 3)
	}
	retry := certificateRetry{Host: p.host}
	if data, err := readCertificateFile(p.path(".renewal.json")); err == nil {
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&retry) != nil || decoder.Decode(new(any)) != io.EOF || retry.Host != p.host || retry.After < 0 || retry.Failures > 12 || retry.Failure != "" && retry.Failure != certificateFailure && retry.Failure != "certificate authority rate limited" {
			return nil, certificateError("invalid retained certificate retry state")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, certificateError("cannot read retained certificate retry state")
	}
	if retry.AccountSHA256 != "" && (retry.AccountSHA256 != certificateFingerprint(account) || retry.KeySHA256 != certificateFingerprint(key)) || retry.AccountSHA256 == "" && retry.KeySHA256 != "" {
		return nil, certificateError("retained certificate identity fingerprint changed; refusing regeneration")
	}
	return &certificateMaterial{account, key, cert, cached, renewAt, retry}, nil
}

func (p *acmeCertificates) CertPair(ctx context.Context, host string) ([]byte, []byte, error) {
	if host != p.host {
		return nil, nil, certificateError("unexpected certificate hostname")
	}
	ctx, cancel := context.WithTimeout(ctx, 90*time.Second)
	stop := context.AfterFunc(p.parent, cancel)
	defer func() { stop(); cancel() }()
	select {
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	case <-p.gate:
	default:
		// Renewal must not stall TLS handshakes that can still use the verified
		// certificate. Stable keys plus an atomic .crt permit safe concurrent reads.
		if err := p.approved(ctx); err != nil {
			return nil, nil, err
		}
		if retained, err := p.retained(); err == nil && retained.cached != nil {
			if err = p.check(ctx); err != nil {
				return nil, nil, err
			}
			return retained.cert, retained.key, nil
		}
		select {
		case <-ctx.Done():
			return nil, nil, ctx.Err()
		case <-p.gate:
		}
	}
	defer func() { p.gate <- struct{}{} }()
	if err := p.approved(ctx); err != nil {
		return nil, nil, err
	}
	material, err := p.retained()
	if err != nil {
		return nil, nil, err
	}
	account, key, cert, cached, renewAt, retry := material.account, material.key, material.cert, material.cached, material.renewAt, material.retry
	now := p.now()
	if len(account) > 0 && len(key) > 0 && retry.AccountSHA256 == "" {
		retry.AccountSHA256, retry.KeySHA256 = certificateFingerprint(account), certificateFingerprint(key)
		if err := p.setRetry(retry); err != nil {
			return nil, nil, err
		}
	}
	if cached != nil && now.Before(renewAt) {
		p.mu.Lock()
		p.retry = certificateRetry{}
		p.mu.Unlock()
		if err := p.check(ctx); err != nil {
			return nil, nil, err
		}
		return cert, key, nil
	}
	p.mu.Lock()
	p.retry = retry
	p.mu.Unlock()
	if retry.After > now.Unix() {
		if err := p.check(ctx); err != nil {
			return nil, nil, err
		}
		if cached != nil {
			return cert, key, nil
		}
		return nil, nil, certificateError("certificate acquisition is deferred by its retained retry limit")
	}
	// Write before any CA operation, including discovery/registration. A crash
	// leaves both the original keys and a conservative five-minute retry floor.
	p.mu.Lock()
	p.inFlight = true
	p.mu.Unlock()
	defer func() { p.mu.Lock(); p.inFlight = false; p.mu.Unlock() }()
	failures := retry.Failures + 1
	if failures > 12 {
		failures = 12
	}
	delay := 5 * time.Minute * time.Duration(uint64(1)<<min(failures-1, 9))
	if delay > 24*time.Hour {
		delay = 24 * time.Hour
	}
	retry = certificateRetry{Host: p.host, After: now.Add(delay).Unix(), Failure: certificateFailure, Failures: failures, AccountSHA256: retry.AccountSHA256, KeySHA256: retry.KeySHA256}
	if err := p.setRetry(retry); err != nil {
		return nil, nil, err
	}
	for _, item := range []struct {
		path string
		data *[]byte
	}{
		{filepath.Join(p.directory, "certs", "acme-account.key.pem"), &account}, {p.path(".key"), &key},
	} {
		if len(*item.data) == 0 {
			data, err := newCertificateKey()
			if err != nil {
				return nil, nil, certificateError("cannot generate a new certificate identity")
			}
			if err = p.check(ctx); err == nil {
				err = writePrivateFile(item.path, data)
			}
			if err != nil {
				return nil, nil, certificateError("cannot persist new certificate identity")
			}
			*item.data = data
		}
	}
	retry.AccountSHA256, retry.KeySHA256 = certificateFingerprint(account), certificateFingerprint(key)
	if err := p.setRetry(retry); err != nil {
		return nil, nil, err
	}
	issued, err := p.issue(ctx, account, key)
	if err != nil {
		var ae *acme.Error
		if errors.As(err, &ae) && ae.StatusCode == http.StatusTooManyRequests {
			delay := retryAfter(ae.Header.Get("Retry-After"), now)
			if candidate := now.Add(delay).Unix(); candidate > retry.After {
				retry.After = candidate
			}
			retry.Failure = "certificate authority rate limited"
		}
		if stateErr := p.setRetry(retry); stateErr != nil {
			return nil, nil, stateErr
		}
		if stateErr := p.approved(ctx); stateErr != nil {
			return nil, nil, stateErr
		}
		if cached != nil {
			if _, err := publicCertificateAt(cert, key, p.host, p.roots, p.now()); err == nil {
				return cert, key, nil
			}
		}
		return nil, nil, certificateError(retry.Failure)
	}
	if err := p.approved(ctx); err != nil {
		return nil, nil, err
	}
	if _, err := publicCertificateAt(issued, key, p.host, p.roots, p.now()); err != nil {
		return nil, nil, certificateError("ACME certificate fails hostname/key/public-chain/time validation")
	}
	if err := writePrivateFile(p.path(".crt"), issued); err != nil {
		return nil, nil, certificateError("cannot commit verified certificate")
	}
	if err := p.check(ctx); err != nil {
		return nil, nil, err
	}
	if err := p.setRetry(certificateRetry{Host: p.host, AccountSHA256: retry.AccountSHA256, KeySHA256: retry.KeySHA256}); err != nil {
		return nil, nil, err
	}
	return issued, key, nil
}

func retryAfter(value string, now time.Time) time.Duration {
	if seconds, err := strconv.ParseInt(value, 10, 32); err == nil && seconds > 0 {
		return time.Duration(seconds) * time.Second
	}
	if date, err := http.ParseTime(value); err == nil && date.After(now) {
		return date.Sub(now)
	}
	return 5 * time.Minute
}

func (p *acmeCertificates) issue(ctx context.Context, account, key []byte) ([]byte, error) {
	accountKey, err := certificateSigner(account)
	if err != nil {
		return nil, err
	}
	leafKey, err := certificateSigner(key)
	if err != nil {
		return nil, err
	}
	client := &acme.Client{Key: accountKey, HTTPClient: p.http, DirectoryURL: p.endpoint, UserAgent: "schemii-android/" + tailscaleVersion,
		RetryBackoff: func(n int, _ *http.Request, res *http.Response) time.Duration {
			if res.StatusCode == http.StatusTooManyRequests || n > 2 {
				return 0
			}
			return time.Duration(n) * time.Second
		}}
	if _, err = client.GetReg(ctx, ""); errors.Is(err, acme.ErrNoAccount) {
		_, err = client.Register(ctx, new(acme.Account), acme.AcceptTOS)
		if errors.Is(err, acme.ErrAccountAlreadyExists) {
			_, err = client.GetReg(ctx, "")
		}
	}
	if err != nil {
		return nil, err
	}
	order, err := client.AuthorizeOrder(ctx, []acme.AuthzID{{Type: "dns", Value: p.host}})
	if err != nil {
		return nil, err
	}
	if len(order.Identifiers) != 1 || order.Identifiers[0].Type != "dns" || order.Identifiers[0].Value != p.host || len(order.AuthzURLs) != 1 {
		return nil, certificateError("unexpected ACME authorization count")
	}
	az, err := client.GetAuthorization(ctx, order.AuthzURLs[0])
	if err != nil {
		return nil, err
	}
	if az.Identifier.Type != "dns" || az.Identifier.Value != p.host || az.Wildcard {
		return nil, certificateError("ACME authorization does not match the approved node")
	}
	if az.Status != acme.StatusPending && az.Status != acme.StatusValid {
		return nil, certificateError("ACME authorization is not usable")
	}
	if az.Status != acme.StatusValid {
		var challenge *acme.Challenge
		for _, candidate := range az.Challenges {
			if candidate.Type == "dns-01" {
				challenge = candidate
				break
			}
		}
		if challenge == nil || !acmeToken.MatchString(challenge.Token) {
			return nil, certificateError("ACME DNS challenge unavailable")
		}
		record, err := client.DNS01ChallengeRecord(challenge.Token)
		if err != nil {
			return nil, err
		}
		if err = p.approved(ctx); err != nil {
			return nil, err
		}
		// Only this exact authenticated hostname is ever sent to SetDNS. The
		// control server independently applies its documented DNS allowlist.
		if err = p.control.SetDNS(ctx, "_acme-challenge."+p.host, record); err != nil {
			return nil, err
		}
		if _, err = client.Accept(ctx, challenge); err != nil {
			return nil, err
		}
	}
	order, err = client.WaitOrder(ctx, order.URI)
	if err != nil {
		return nil, err
	}
	csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{DNSNames: []string{p.host}}, leafKey)
	if err != nil {
		return nil, err
	}
	der, _, err := client.CreateOrderCert(ctx, order.FinalizeURL, csr, true)
	if err != nil {
		return nil, err
	}
	var result bytes.Buffer
	for _, cert := range der {
		if err = pem.Encode(&result, &pem.Block{Type: "CERTIFICATE", Bytes: cert}); err != nil {
			return nil, err
		}
	}
	return result.Bytes(), nil
}
