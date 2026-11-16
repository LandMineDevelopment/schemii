package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/client/local"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/net/netmon"
)

const certificateTestHost = "apk.tail123.ts.net"

// The fake CA implements real directory/account/JWS/order/dns-01/finalize/CSR
// exchanges. Only its endpoints and trust roots are substituted. No enrollment,
// public ACME/control request, SDK certificate success stub or secret is used.
type acmeFixture struct {
	t                                                     *testing.T
	mu                                                    sync.Mutex
	ca                                                    testAuthority
	url                                                   string
	client                                                *local.Client
	roots                                                 *x509.CertPool
	clock                                                 atomic.Int64
	requests, orders, records                             int
	account                                               map[string]string
	public                                                *ecdsa.PublicKey
	txt                                                   string
	accepted, registered                                  bool
	chain                                                 []byte
	rate, foreignAuthorization, wrongCertificate, denyDNS bool
	futureCertificate                                     bool
	identity                                              string
	onCertificate                                         func()
	blockDirectory                                        chan struct{}
	entered                                               chan struct{}
}

func newACMEFixture(t *testing.T) *acmeFixture {
	t.Helper()
	f := &acmeFixture{t: t, ca: authority(t), identity: certificateTestHost}
	f.clock.Store(time.Now().UnixNano())
	f.roots = x509.NewCertPool()
	f.roots.AppendCertsFromPEM(f.ca.pem)
	server := backend(t, f.ca, "localhost", http.HandlerFunc(f.serveACME))
	u, _ := url.Parse(server.URL)
	u.Host = net.JoinHostPort("localhost", u.Port())
	f.configure(func() { f.url = u.String() })
	control := httptest.NewServer(http.HandlerFunc(f.serveControl))
	t.Cleanup(control.Close)
	controlURL, _ := url.Parse(control.URL)
	f.client = &local.Client{UseSocketOnly: true, OmitAuth: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", controlURL.Host)
	}}
	return f
}

func (f *acmeFixture) counts() (int, int, int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.requests, f.orders, f.records
}
func (f *acmeFixture) configure(change func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	change()
}
func (f *acmeFixture) requestCount() int { n, _, _ := f.counts(); return n }
func (f *acmeFixture) now() time.Time    { return time.Unix(0, f.clock.Load()) }

func (f *acmeFixture) serveControl(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch r.URL.Path {
	case "/localapi/v0/status":
		json.NewEncoder(w).Encode(&ipnstate.Status{BackendState: "Running", Self: &ipnstate.PeerStatus{DNSName: f.identity + ".", Online: true}, CurrentTailnet: &ipnstate.TailnetStatus{MagicDNSEnabled: true}, CertDomains: []string{f.identity}})
	case "/localapi/v0/set-dns":
		if f.denyDNS {
			http.Error(w, "denied", http.StatusForbidden)
			return
		}
		if r.Method != "POST" || r.FormValue("name") != "_acme-challenge."+certificateTestHost {
			f.t.Error("unexpected DNS method/name")
			http.Error(w, "bad DNS", 400)
			return
		}
		f.records++
		f.txt = r.FormValue("value")
		io.WriteString(w, "{}")
	default:
		http.NotFound(w, r)
	}
}

func (f *acmeFixture) signedPayload(r *http.Request) ([]byte, error) {
	var jws struct{ Protected, Payload, Signature string }
	if err := json.NewDecoder(r.Body).Decode(&jws); err != nil {
		return nil, err
	}
	decode := base64.RawURLEncoding.DecodeString
	protected, err := decode(jws.Protected)
	if err != nil {
		return nil, err
	}
	var header struct {
		JWK                  map[string]string `json:"jwk"`
		KID, Alg, Nonce, URL string
	}
	if err = json.Unmarshal(protected, &header); err != nil {
		return nil, err
	}
	if header.URL != f.url+r.URL.RequestURI() || header.Alg != "ES256" || header.Nonce == "" {
		return nil, errors.New("invalid JWS metadata")
	}
	public := f.public
	if header.JWK != nil {
		x, err := decode(header.JWK["x"])
		if err != nil {
			return nil, err
		}
		y, err := decode(header.JWK["y"])
		if err != nil {
			return nil, err
		}
		public = &ecdsa.PublicKey{Curve: elliptic.P256(), X: new(big.Int).SetBytes(x), Y: new(big.Int).SetBytes(y)}
		if !public.Curve.IsOnCurve(public.X, public.Y) {
			return nil, errors.New("invalid account point")
		}
		if f.public != nil && !f.public.Equal(public) {
			return nil, errors.New("account identity changed")
		}
		f.public = public
		f.account = header.JWK
	} else if header.KID != f.url+"/account/1" {
		return nil, errors.New("unknown account")
	}
	sig, err := decode(jws.Signature)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256([]byte(jws.Protected + "." + jws.Payload))
	if public == nil || len(sig) != 64 || !ecdsa.Verify(public, hash[:], new(big.Int).SetBytes(sig[:32]), new(big.Int).SetBytes(sig[32:])) {
		return nil, errors.New("invalid ACME signature")
	}
	return decode(jws.Payload)
}

func (f *acmeFixture) order(status string) map[string]any {
	return map[string]any{"status": status, "identifiers": []map[string]string{{"type": "dns", "value": certificateTestHost}}, "authorizations": []string{f.url + "/authorization/1"}, "finalize": f.url + "/finalize/1", "certificate": f.url + "/certificate/1"}
}

func (f *acmeFixture) serveACME(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	blocked, entered := f.blockDirectory, f.entered
	f.mu.Unlock()
	if r.URL.Path == "/directory" && blocked != nil {
		select {
		case entered <- struct{}{}:
		default:
		}
		select {
		case <-blocked:
		case <-r.Context().Done():
			return
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.requests++
	w.Header().Set("Replay-Nonce", base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprint(f.requests))))
	w.Header().Set("Content-Type", "application/json")
	if r.URL.Path == "/directory" {
		json.NewEncoder(w).Encode(map[string]string{"newNonce": f.url + "/nonce", "newAccount": f.url + "/new-account", "newOrder": f.url + "/new-order"})
		return
	}
	if r.URL.Path == "/nonce" {
		return
	}
	payload, err := f.signedPayload(r)
	if err != nil {
		f.t.Error(err)
		http.Error(w, "invalid JWS", 400)
		return
	}
	switch r.URL.Path {
	case "/new-account":
		var data struct {
			OnlyExisting bool `json:"onlyReturnExisting"`
		}
		json.Unmarshal(payload, &data)
		if data.OnlyExisting && !f.registered {
			w.WriteHeader(400)
			io.WriteString(w, `{"type":"urn:ietf:params:acme:error:accountDoesNotExist","detail":"owned fixture"}`)
			return
		}
		w.Header().Set("Location", f.url+"/account/1")
		if !f.registered {
			w.WriteHeader(201)
			f.registered = true
		}
		io.WriteString(w, `{"status":"valid"}`)
	case "/new-order":
		if f.rate {
			w.Header().Set("Retry-After", "3600")
			w.WriteHeader(429)
			io.WriteString(w, `{"type":"urn:ietf:params:acme:error:rateLimited","detail":"do not disclose fixture token"}`)
			return
		}
		var data struct {
			Identifiers []struct{ Type, Value string }
		}
		json.Unmarshal(payload, &data)
		if len(data.Identifiers) != 1 || data.Identifiers[0].Type != "dns" || data.Identifiers[0].Value != certificateTestHost {
			f.t.Error("unauthorized CSR identifiers")
			http.Error(w, "bad identifiers", 400)
			return
		}
		f.orders++
		f.accepted = false
		f.txt = ""
		w.Header().Set("Location", f.url+"/order/1")
		w.WriteHeader(201)
		json.NewEncoder(w).Encode(f.order("pending"))
	case "/authorization/1":
		host := certificateTestHost
		if f.foreignAuthorization {
			host = "other.tail123.ts.net"
		}
		json.NewEncoder(w).Encode(map[string]any{"status": "pending", "identifier": map[string]string{"type": "dns", "value": host}, "challenges": []map[string]string{{"type": "dns-01", "url": f.url + "/challenge/1", "token": "owned-fixture-challenge"}}})
	case "/challenge/1":
		jwk, _ := json.Marshal(f.account)
		thumb := sha256.Sum256(jwk)
		sum := sha256.Sum256([]byte("owned-fixture-challenge." + base64.RawURLEncoding.EncodeToString(thumb[:])))
		if f.txt != base64.RawURLEncoding.EncodeToString(sum[:]) {
			f.t.Error("DNS challenge did not match signed account")
			http.Error(w, "DNS not set", 400)
			return
		}
		f.accepted = true
		io.WriteString(w, `{"type":"dns-01","status":"valid"}`)
	case "/order/1":
		state := "pending"
		if f.accepted {
			state = "ready"
		}
		json.NewEncoder(w).Encode(f.order(state))
	case "/finalize/1":
		if !f.accepted {
			f.t.Error("finalize before DNS proof")
			http.Error(w, "unready", 400)
			return
		}
		var data struct{ CSR string }
		json.Unmarshal(payload, &data)
		raw, err := base64.RawURLEncoding.DecodeString(data.CSR)
		csr, parseErr := x509.ParseCertificateRequest(raw)
		if err != nil || parseErr != nil || csr.CheckSignature() != nil || len(csr.DNSNames) != 1 || csr.DNSNames[0] != certificateTestHost {
			f.t.Error("invalid CSR/signature/domain")
			http.Error(w, "bad CSR", 400)
			return
		}
		host := certificateTestHost
		if f.wrongCertificate {
			host = "other.tail123.ts.net"
		}
		leaf := &x509.Certificate{SerialNumber: big.NewInt(int64(10 + f.orders)), DNSNames: []string{host}, NotBefore: time.Now().Add(-time.Minute), NotAfter: f.now().Add(30 * time.Minute), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
		if f.futureCertificate {
			leaf.NotBefore = time.Now().Add(10 * time.Minute)
		}
		der, err := x509.CreateCertificate(rand.Reader, leaf, f.ca.cert, csr.PublicKey, f.ca.key)
		if err != nil {
			f.t.Error(err)
			http.Error(w, "sign failed", 500)
			return
		}
		f.chain = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
		json.NewEncoder(w).Encode(f.order("valid"))
	case "/certificate/1":
		if f.onCertificate != nil {
			f.onCertificate()
		}
		w.Header().Set("Content-Type", "application/pem-certificate-chain")
		w.Write(f.chain)
	default:
		f.t.Error("unexpected ACME endpoint")
		http.NotFound(w, r)
	}
}

func fixtureProvider(t *testing.T, f *acmeFixture, ctx context.Context, current func() error) *acmeCertificates {
	t.Helper()
	monitor := netmon.NewStatic()
	t.Cleanup(func() { monitor.Close() })
	client, close, err := certificateHTTPClient(ctx, f.url+"/directory", f.roots, monitor, current)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(close)
	dir := filepath.Join(t.TempDir(), "node")
	if err := privateDir(dir); err != nil {
		t.Fatal(err)
	}
	p, err := newACMECertificates(ctx, f.client, dir, certificateTestHost, f.url+"/directory", f.roots, client, current)
	if err != nil {
		t.Fatal(err)
	}
	p.now = f.now
	return p
}

// Also called under the source-extracted Android SDK netns overlay.
func exerciseACMEIdentityAndRenewal(t *testing.T) {
	f := newACMEFixture(t)
	p := fixtureProvider(t, f, context.Background(), func() error { return nil })
	first, err := fetchPublicCertificate(context.Background(), p, p.directory, certificateTestHost, f.roots)
	if err != nil {
		t.Fatal(err)
	}
	account, _ := os.ReadFile(filepath.Join(p.directory, "certs/acme-account.key.pem"))
	key, _ := os.ReadFile(p.path(".key"))
	cert, _ := os.ReadFile(p.path(".crt"))
	requests := f.requestCount()
	if _, _, err := p.CertPair(context.Background(), certificateTestHost); err != nil || f.requestCount() != requests {
		t.Fatal("cache was not reused", err)
	}
	f.clock.Add(int64(22 * time.Minute))
	if _, _, err := p.CertPair(context.Background(), certificateTestHost); err != nil {
		t.Fatal(err)
	}
	next, _ := os.ReadFile(p.path(".crt"))
	nextKey, _ := os.ReadFile(p.path(".key"))
	nextAccount, _ := os.ReadFile(filepath.Join(p.directory, "certs/acme-account.key.pem"))
	_, orders, records := f.counts()
	if string(key) != string(nextKey) || string(account) != string(nextAccount) || string(cert) == string(next) || orders != 2 || records != 2 {
		t.Fatal("renewal changed identity or skipped real protocol")
	}
	if first.Leaf.DNSNames[0] != certificateTestHost {
		t.Fatal("wrong verified hostname")
	}
	for _, name := range []string{p.path(".key"), p.path(".crt"), p.path(".renewal.json"), filepath.Join(p.directory, "certs/acme-account.key.pem")} {
		info, err := os.Stat(name)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatal("certificate state is not private", err)
		}
	}
}

func TestACMEProtocolIdentityCacheAndRenewal(t *testing.T) { exerciseACMEIdentityAndRenewal(t) }

func TestACMERateLimitRetainsKeysAndValidCertificate(t *testing.T) {
	f := newACMEFixture(t)
	p := fixtureProvider(t, f, context.Background(), func() error { return nil })
	cert, key, err := p.CertPair(context.Background(), certificateTestHost)
	if err != nil {
		t.Fatal(err)
	}
	f.clock.Add(int64(22 * time.Minute))
	f.configure(func() { f.rate = true })
	retained, retainedKey, err := p.CertPair(context.Background(), certificateTestHost)
	if err != nil || string(retained) != string(cert) || string(retainedKey) != string(key) {
		t.Fatal("valid cache lost during renewal failure", err)
	}
	warning, retry := p.RenewalState()
	if warning != "certificate authority rate limited" || retry < p.now().Add(time.Hour).Unix() {
		t.Fatal("rate limit not retained", warning, retry)
	}
	requests := f.requestCount()
	if _, _, err := p.CertPair(context.Background(), certificateTestHost); err != nil || f.requestCount() != requests {
		t.Fatal("rate limit retried", err)
	}
	f.clock.Add(int64(10 * time.Minute))
	if _, _, err := p.CertPair(context.Background(), certificateTestHost); err == nil || f.requestCount() != requests {
		t.Fatal("expired certificate survived rate limit")
	}
	// A new provider reads the durable checkpoint instead of starting orders.
	restarted, err := newACMECertificates(p.parent, p.control, p.directory, p.host, p.endpoint, p.roots, p.http, p.current)
	if err != nil {
		t.Fatal(err)
	}
	restarted.now = f.now
	if _, _, err := restarted.CertPair(context.Background(), certificateTestHost); err == nil || f.requestCount() != requests {
		t.Fatal("restart bypassed retry checkpoint")
	}
}

func TestACMERejectsAuthorityIdentityAndStateFailures(t *testing.T) {
	for _, test := range []string{"unapproved", "foreign_authorization", "wrong_certificate", "wrong_chain", "future_certificate", "DNS_denied", "empty_account", "symlink_key", "epoch_before_commit"} {
		t.Run(test, func(t *testing.T) {
			f := newACMEFixture(t)
			var revoked atomic.Bool
			p := fixtureProvider(t, f, context.Background(), func() error {
				if revoked.Load() {
					return errors.New("revoked")
				}
				return nil
			})
			f.configure(func() {
				switch test {
				case "unapproved":
					f.identity = "other.tail123.ts.net"
				case "foreign_authorization":
					f.foreignAuthorization = true
				case "wrong_certificate":
					f.wrongCertificate = true
				case "wrong_chain":
					f.ca = authority(t)
				case "future_certificate":
					f.futureCertificate = true
				case "DNS_denied":
					f.denyDNS = true
				case "epoch_before_commit":
					f.onCertificate = func() { revoked.Store(true) }
				case "empty_account", "symlink_key":
					if err := privateDir(filepath.Join(p.directory, "certs")); err != nil {
						t.Fatal(err)
					}
					if test == "empty_account" {
						os.WriteFile(filepath.Join(p.directory, "certs/acme-account.key.pem"), nil, 0600)
					} else {
						os.Symlink(filepath.Join(t.TempDir(), "unowned"), p.path(".key"))
					}
				}
			})
			if _, _, err := p.CertPair(context.Background(), certificateTestHost); err == nil {
				t.Fatal("unsafe certificate path succeeded")
			}
			if _, err := os.Stat(p.path(".crt")); !os.IsNotExist(err) {
				t.Fatal("failed issuance committed certificate", err)
			}
			if test == "unapproved" || test == "empty_account" || test == "symlink_key" {
				if f.requestCount() != 0 {
					t.Fatal("invalid identity touched CA")
				}
			}
			_, _, records := f.counts()
			if test == "foreign_authorization" && records != 0 {
				t.Fatal("foreign domain touched DNS")
			}
		})
	}
}

func TestACMETransportRejectsOtherOriginsAndTrust(t *testing.T) {
	f := newACMEFixture(t)
	monitor := netmon.NewStatic()
	defer monitor.Close()
	client, close, err := certificateHTTPClient(context.Background(), f.url+"/directory", authority(t).pool(), monitor, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	defer close()
	if _, err := client.Get(f.url + "/directory"); err == nil {
		t.Fatal("untrusted CA accepted")
	}
	for _, target := range []string{"http://localhost/directory", "https://unowned.invalid/directory"} {
		if _, err := client.Get(target); err == nil {
			t.Fatal("unapproved endpoint accepted")
		}
	}
}

func TestACMECancellationUnblocksOwnedRequestAndWaiter(t *testing.T) {
	f := newACMEFixture(t)
	f.configure(func() { f.blockDirectory = make(chan struct{}); f.entered = make(chan struct{}, 1) })
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	p := fixtureProvider(t, f, parent, func() error { return nil })
	done := make(chan error, 1)
	go func() { _, _, err := p.CertPair(context.Background(), certificateTestHost); done <- err }()
	select {
	case <-f.entered:
	case <-time.After(3 * time.Second):
		t.Fatal("ACME request did not start")
	}
	queued, stop := context.WithCancel(context.Background())
	stop()
	if _, _, err := p.CertPair(queued, certificateTestHost); !errors.Is(err, context.Canceled) {
		t.Fatal("queued cancellation blocked", err)
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled request succeeded")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("parent cancellation did not unwind")
	}
	if _, err := os.Stat(p.path(".crt")); !os.IsNotExist(err) {
		t.Fatal("cancelled issuance committed")
	}
}

func TestACMERetainedIdentityErrorsNeverRegenerateKeys(t *testing.T) {
	for _, damage := range []string{"missing_account", "missing_leaf_key", "missing_leaf_and_cert", "missing_all", "replaced_account", "malformed_account", "mismatched_leaf_key", "invalid_retry_file"} {
		t.Run(damage, func(t *testing.T) {
			f := newACMEFixture(t)
			p := fixtureProvider(t, f, context.Background(), func() error { return nil })
			if _, _, err := p.CertPair(context.Background(), p.host); err != nil {
				t.Fatal(err)
			}
			paths := []string{filepath.Join(p.directory, "certs/acme-account.key.pem"), p.path(".key"), p.path(".crt"), p.path(".renewal.json")}
			switch damage {
			case "missing_account":
				if err := os.Remove(paths[0]); err != nil {
					t.Fatal(err)
				}
			case "missing_leaf_key":
				if err := os.Remove(paths[1]); err != nil {
					t.Fatal(err)
				}
			case "missing_leaf_and_cert", "missing_all":
				first := 1
				if damage == "missing_all" {
					first = 0
				}
				for _, path := range paths[first:3] {
					if err := os.Remove(path); err != nil {
						t.Fatal(err)
					}
				}
			case "replaced_account":
				key, err := newCertificateKey()
				if err != nil {
					t.Fatal(err)
				}
				if err = os.WriteFile(paths[0], key, 0600); err != nil {
					t.Fatal(err)
				}
			case "malformed_account":
				if err := os.WriteFile(paths[0], []byte("unreadable retained key"), 0600); err != nil {
					t.Fatal(err)
				}
			case "mismatched_leaf_key":
				key, err := newCertificateKey()
				if err != nil {
					t.Fatal(err)
				}
				if err = os.WriteFile(paths[1], key, 0600); err != nil {
					t.Fatal(err)
				}
			case "invalid_retry_file":
				if err := os.WriteFile(paths[3], []byte("invalid metadata"), 0600); err != nil {
					t.Fatal(err)
				}
				f.clock.Add(int64(22 * time.Minute))
			}
			before := map[string]string{}
			for _, path := range paths {
				raw, _ := os.ReadFile(path)
				before[path] = string(raw)
			}
			requests := f.requestCount()
			if _, _, err := p.CertPair(context.Background(), p.host); err == nil {
				t.Fatal("inaccessible identity was accepted")
			}
			for _, path := range paths {
				raw, _ := os.ReadFile(path)
				if string(raw) != before[path] {
					t.Fatal("retained state was replaced", filepath.Base(path))
				}
			}
			if f.requestCount() != requests {
				t.Fatal("invalid retained state reached CA")
			}
		})
	}
}

func TestACMEChallengeHostAndCancellationAreRecheckedBeforeCache(t *testing.T) {
	f := newACMEFixture(t)
	p := fixtureProvider(t, f, context.Background(), func() error { return nil })
	if _, _, err := p.CertPair(context.Background(), p.host); err != nil {
		t.Fatal(err)
	}
	requests := f.requestCount()
	f.configure(func() { f.identity = "other.tail123.ts.net" })
	if _, _, err := p.CertPair(context.Background(), p.host); err == nil {
		t.Fatal("cache ignored changed approved identity")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := p.CertPair(ctx, p.host); !errors.Is(err, context.Canceled) {
		t.Fatal("cache ignored caller cancellation")
	}
	if f.requestCount() != requests {
		t.Fatal("stale cached request reached CA")
	}
}

func TestACMERenewsIdleCertificateThroughSharedVerificationAndStops(t *testing.T) {
	f := newACMEFixture(t)
	p := fixtureProvider(t, f, context.Background(), func() error { return nil })
	first, err := fetchPublicCertificate(context.Background(), p, p.directory, p.host, p.roots)
	if err != nil {
		t.Fatal(err)
	}
	run := filepath.Join(t.TempDir(), "run")
	if err := privateDir(run); err != nil {
		t.Fatal(err)
	}
	store := newStatusStore(filepath.Join(run, "tailnet.json"))
	f.clock.Add(int64(22 * time.Minute))
	failures := make(chan error, 1)
	stop := p.startRenewal(store, failures, 5*time.Millisecond)
	defer stop()
	deadline := time.NewTimer(3 * time.Second)
	defer deadline.Stop()
	for {
		store.mu.Lock()
		expires, ready := store.value.CertificateExpiresAt, store.value.CertificateReady
		store.mu.Unlock()
		if ready && expires > first.Leaf.NotAfter.Unix() {
			break
		}
		select {
		case err := <-failures:
			t.Fatal(err)
		case <-deadline.C:
			t.Fatal("idle certificate did not renew")
		case <-time.After(5 * time.Millisecond):
		}
	}
	stop()
	_, orders, records := f.counts()
	if orders != 2 || records != 2 {
		t.Fatal("idle renewal did not use real ACME/DNS protocol", orders, records)
	}
}

func TestACMEValidCacheDoesNotWaitForBlockedRenewal(t *testing.T) {
	f := newACMEFixture(t)
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	p := fixtureProvider(t, f, parent, func() error { return nil })
	if _, _, err := p.CertPair(context.Background(), p.host); err != nil {
		t.Fatal(err)
	}
	f.configure(func() { f.blockDirectory = make(chan struct{}); f.entered = make(chan struct{}, 1) })
	f.clock.Add(int64(22 * time.Minute))
	done := make(chan error, 1)
	go func() { _, _, err := p.CertPair(context.Background(), p.host); done <- err }()
	select {
	case <-f.entered:
	case <-time.After(3 * time.Second):
		t.Fatal("renewal did not start")
	}
	ctx, stop := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer stop()
	if _, err := fetchPublicCertificate(ctx, p, p.directory, p.host, p.roots); err != nil {
		t.Fatal("valid TLS cache blocked behind renewal", err)
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled renewal succeeded")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("renewal did not cancel")
	}
}

func (a testAuthority) pool() *x509.CertPool {
	p := x509.NewCertPool()
	p.AppendCertsFromPEM(a.pem)
	return p
}
