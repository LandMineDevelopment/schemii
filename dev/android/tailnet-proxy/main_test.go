package main

import (
	"bufio"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"io"
	"log"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
)

func validConfig(t *testing.T) config {
	t.Helper()
	root := t.TempDir()
	return config{StateDir: filepath.Join(root, "node"), StatusFile: filepath.Join(root, "run", "tailnet.json"),
		Hostname: "schemii-phone", Upstream: "https://127.0.0.1:8001", UpstreamCA: filepath.Join(root, "ca.crt")}
}

func TestConfigurationRejectsUnsafeTargetsAndPaths(t *testing.T) {
	cfg := validConfig(t)
	if _, err := cfg.validate(); err != nil {
		t.Fatal(err)
	}
	cases := map[string]func(*config){
		"HTTP":               func(c *config) { c.Upstream = "http://127.0.0.1:8001" },
		"external":           func(c *config) { c.Upstream = "https://example.com:8001" },
		"metadata":           func(c *config) { c.Upstream = "https://127.0.0.1:5433" },
		"sidecar":            func(c *config) { c.Upstream = "https://127.0.0.1:4097" },
		"credentials":        func(c *config) { c.Upstream = "https://user:secret@127.0.0.1:8001" },
		"path":               func(c *config) { c.Upstream += "/api" },
		"query":              func(c *config) { c.Upstream += "?secret=x" },
		"fragment":           func(c *config) { c.Upstream += "#x" },
		"relative_state":     func(c *config) { c.StateDir = "node" },
		"root_state":         func(c *config) { c.StateDir = "/" },
		"unclean":            func(c *config) { c.StatusFile = "/tmp/../run/tailnet.json" },
		"status_in_state":    func(c *config) { c.StatusFile = filepath.Join(c.StateDir, "nested", "tailnet.json") },
		"status_filename":    func(c *config) { c.StatusFile = filepath.Join(filepath.Dir(c.StatusFile), "config.json") },
		"bad_hostname":       func(c *config) { c.Hostname = "phone.ts.net" },
		"uppercase_hostname": func(c *config) { c.Hostname = "Phone" },
		"hyphen_hostname":    func(c *config) { c.Hostname = "-phone" },
		"long_hostname":      func(c *config) { c.Hostname = strings.Repeat("a", 64) },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			copy := cfg
			change(&copy)
			if _, err := copy.validate(); err == nil {
				t.Fatal("unsafe configuration accepted")
			}
		})
	}
}

func TestAuthEnvironmentIsNotConsumed(t *testing.T) {
	t.Setenv("TS_AUTHKEY", "test-only-not-a-credential")
	if err := rejectAuthEnvironment(); err == nil || strings.Contains(err.Error(), "test-only") {
		t.Fatal("secret environment was accepted or disclosed")
	}
}

func TestPrivatePathsAndExclusiveNodeLock(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, "node")
	if err := privateDir(directory); err != nil {
		t.Fatal(err)
	}
	if info, _ := os.Stat(directory); info.Mode().Perm() != 0700 {
		t.Fatal("node directory is not private")
	}
	shared := filepath.Join(root, "shared")
	if err := os.Mkdir(shared, 0755); err != nil {
		t.Fatal(err)
	}
	if err := privateDir(shared); err == nil {
		t.Fatal("nonprivate existing directory accepted")
	}
	if info, _ := os.Stat(shared); info.Mode().Perm() != 0755 {
		t.Fatal("unrelated directory permissions changed")
	}
	first, err := lockNode(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := lockNode(directory); err == nil {
		second.Close()
		t.Fatal("duplicate identity was not fenced")
	}
	first.Close()
	second, err := lockNode(directory)
	if err != nil {
		t.Fatal(err)
	}
	second.Close()
	link := filepath.Join(root, "link")
	if err := os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	if err := privateDir(filepath.Join(link, "child")); err == nil {
		t.Fatal("symlinked private directory was accepted")
	}
	if err := os.Remove(filepath.Join(directory, "schemii.lock")); err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(root, "victim")
	if err := os.WriteFile(victim, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(victim, filepath.Join(directory, "schemii.lock")); err != nil {
		t.Fatal(err)
	}
	if lock, err := lockNode(directory); err == nil {
		lock.Close()
		t.Fatal("symlinked lock accepted")
	}
	if got, _ := os.ReadFile(victim); string(got) != "preserve" {
		t.Fatal("victim changed")
	}
}

func TestPrivateStatusAtomicTransitionsAndNoUnchangedChurn(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tailnet.json")
	store := newStatusStore(path)
	if err := store.update(func(v *status) { v.Online = true; v.Listening = true; v.CertificateReady = true }); err != nil {
		t.Fatal(err)
	}
	first, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if first.Mode().Perm() != 0600 {
		t.Fatal("status is not private")
	}
	if err := store.update(func(*status) {}); err != nil {
		t.Fatal(err)
	}
	same, _ := os.Stat(path)
	if !os.SameFile(first, same) {
		t.Fatal("unchanged observation rewrote status")
	}
	for _, online := range []bool{false, true} {
		if err := store.update(func(v *status) { v.Online = online }); err != nil {
			t.Fatal(err)
		}
		var observed status
		bytes, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(bytes, &observed); err != nil {
			t.Fatal(err)
		}
		if observed.Ready != online || !observed.Listening || observed.Schema != 1 || observed.PID != os.Getpid() {
			t.Fatalf("incorrect live transition: %#v", observed)
		}
	}
	victim := filepath.Join(t.TempDir(), "victim")
	os.WriteFile(victim, []byte("preserve"), 0600)
	os.Remove(path)
	os.Symlink(victim, path)
	if err := writeStatus(path, status{Schema: 1}); err == nil {
		t.Fatal("symlink status destination accepted")
	}
	if got, _ := os.ReadFile(victim); string(got) != "preserve" {
		t.Fatal("victim overwritten")
	}
}

func TestIdentityRequiresActualTailnetDNS(t *testing.T) {
	st := &ipnstate.Status{Self: &ipnstate.PeerStatus{DNSName: "schemii-phone.tail123.ts.net."}, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.3")}}
	host, origin, ips := identity(st)
	if host != "schemii-phone.tail123.ts.net" || origin != "https://schemii-phone.tail123.ts.net" || len(ips) != 1 || ips[0] != "100.64.0.3" {
		t.Fatal("incorrect authenticated identity")
	}
	for _, bad := range []string{"", "phone.ts.net.evil.example", "https://phone.ts.net", "phone:443.ts.net", "*.ts.net"} {
		st.Self.DNSName = bad
		if _, origin, _ := identity(st); origin != "" {
			t.Fatal("invalid DNS identity accepted")
		}
	}
}

type testAuthority struct {
	key  *ecdsa.PrivateKey
	cert *x509.Certificate
	pem  []byte
}

func authority(t *testing.T) testAuthority {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Proxy unit test CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return testAuthority{key, cert, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})}
}

func (a testAuthority) leaf(t *testing.T, host string) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: []string{host}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, a.cert, &key.PublicKey, a.key)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
}

func backend(t *testing.T, a testAuthority, hostname string, handler http.Handler) *httptest.Server {
	t.Helper()
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{Certificates: []tls.Certificate{a.leaf(t, hostname)}, MinVersion: tls.VersionTLS12}
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func TestProxyVerifiesCAHostnameAndPreservesRequestSemantics(t *testing.T) {
	trusted := authority(t)
	var received atomic.Int64
	server := backend(t, trusted, "localhost", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received.Add(1)
		if r.Host != "schemii-phone.tail123.ts.net" || r.Header.Get("X-Forwarded-Host") != r.Host || r.Header.Get("X-Forwarded-Proto") != "https" || r.Header.Get("X-Forwarded-For") != "100.64.0.2" || r.Header.Get("Origin") != "https://schemii-phone.tail123.ts.net" || r.URL.RequestURI() != "/api/v1/readiness?probe=1" {
			t.Errorf("request or forwarded security context changed: host=%s headers=%v url=%s", r.Host, r.Header, r.URL.RequestURI())
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.WriteHeader(http.StatusUnauthorized)
		io.WriteString(w, "application result\n")
	}))
	target, _ := url.Parse(server.URL)
	handler, transport, err := proxyHandler(target, trusted.pem, "schemii-phone.tail123.ts.net")
	if err != nil {
		t.Fatal(err)
	}
	defer transport.CloseIdleConnections()
	r := httptest.NewRequest("GET", "https://schemii-phone.tail123.ts.net/api/v1/readiness?probe=1", nil)
	r.RemoteAddr = "100.64.0.2:12345"
	r.Header.Set("Origin", "https://schemii-phone.tail123.ts.net")
	r.Header.Set("X-Forwarded-For", "attacker")
	r.Header.Set("X-Forwarded-Proto", "http")
	r.Header.Set("X-Forwarded-Host", "attacker")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized || w.Body.String() != "application result\n" || received.Load() != 1 {
		t.Fatalf("application response changed: %d %q", w.Code, w.Body.String())
	}
	badRequest := r.Clone(context.Background())
	badRequest.Host = "attacker.example"
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, badRequest)
	if w.Code != http.StatusMisdirectedRequest || received.Load() != 1 {
		t.Fatal("unknown host was forwarded")
	}
	for _, test := range []struct {
		name    string
		ca      []byte
		backend *httptest.Server
	}{
		{"wrong_ca", authority(t).pem, server},
		{"wrong_hostname", trusted.pem, backend(t, trusted, "different.example", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("bad hostname reached application") }))},
	} {
		t.Run(test.name, func(t *testing.T) {
			u, _ := url.Parse(test.backend.URL)
			h, tr, err := proxyHandler(u, test.ca, "schemii-phone.tail123.ts.net")
			if err != nil {
				t.Fatal(err)
			}
			defer tr.CloseIdleConnections()
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if w.Code != http.StatusBadGateway || strings.Contains(w.Body.String(), "x509") || strings.Contains(w.Body.String(), test.backend.URL) {
				t.Fatal("invalid upstream accepted or private failure disclosed")
			}
		})
	}
}

func TestProxyFlushesStreamingRowsBeforeCompletion(t *testing.T) {
	trusted := authority(t)
	release := make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	t.Cleanup(unblock)
	server := backend(t, trusted, "localhost", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		io.WriteString(w, "first\n")
		w.(http.Flusher).Flush()
		select {
		case <-release:
			io.WriteString(w, "second\n")
		case <-r.Context().Done():
		}
	}))
	target, _ := url.Parse(server.URL)
	handler, tr, err := proxyHandler(target, trusted.pem, "schemii-phone.tail123.ts.net")
	if err != nil {
		t.Fatal(err)
	}
	defer tr.CloseIdleConnections()
	front := httptest.NewServer(handler)
	defer front.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	r, _ := http.NewRequestWithContext(ctx, "GET", front.URL+"/stream", nil)
	r.Host = "schemii-phone.tail123.ts.net"
	response, err := front.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	first, err := reader.ReadString('\n')
	if err != nil || first != "first\n" {
		t.Fatalf("stream buffered until completion: %q %v", first, err)
	}
	unblock()
	second, err := reader.ReadString('\n')
	if err != nil || second != "second\n" {
		t.Fatalf("stream completion lost: %q %v", second, err)
	}
}

func TestBadCAStopsBeforeNodeCreation(t *testing.T) {
	cfg := validConfig(t)
	os.WriteFile(cfg.UpstreamCA, []byte("not a certificate"), 0600)
	if err := run(context.Background(), cfg); err == nil {
		t.Fatal("invalid CA was accepted")
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, "tailscaled.state")); !os.IsNotExist(err) {
		t.Fatal("node state was created before trust preflight")
	}
	if _, err := os.Stat(cfg.StatusFile); !os.IsNotExist(err) {
		t.Fatal("invalid config wrote status")
	}
}

type fakeCertificateClient struct {
	cert, key []byte
	err       error
}

func (client fakeCertificateClient) CertPair(context.Context, string) ([]byte, []byte, error) {
	return client.cert, client.key, client.err
}

func certificateClientFor(t *testing.T, a testAuthority, host string) fakeCertificateClient {
	t.Helper()
	leaf := a.leaf(t, host)
	der, err := x509.MarshalPKCS8PrivateKey(leaf.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	cert := append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: leaf.Certificate[0]}), a.pem...)
	return fakeCertificateClient{cert: cert, key: pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})}
}

func TestCertificateReadinessRequiresTrustedChainAndActualHostname(t *testing.T) {
	a := authority(t)
	client := certificateClientFor(t, a, "schemii-phone.tail123.ts.net")
	roots := x509.NewCertPool()
	roots.AddCert(a.cert)
	if err := verifyPublicCertificate(context.Background(), client, t.TempDir(), "schemii-phone.tail123.ts.net", roots); err != nil {
		t.Fatal(err)
	}
	if err := verifyPublicCertificate(context.Background(), client, t.TempDir(), "different.tail123.ts.net", roots); err == nil {
		t.Fatal("certificate hostname mismatch accepted")
	}
	if err := verifyPublicCertificate(context.Background(), client, t.TempDir(), "schemii-phone.tail123.ts.net", nil); err == nil {
		t.Fatal("private test CA was accepted by system/public trust check")
	}
	other := certificateClientFor(t, a, "schemii-phone.tail123.ts.net")
	client.key = other.key
	if err := verifyPublicCertificate(context.Background(), client, t.TempDir(), "schemii-phone.tail123.ts.net", roots); err == nil {
		t.Fatal("mismatched certificate private key accepted")
	}
	if err := verifyPublicCertificate(context.Background(), fakeCertificateClient{err: context.DeadlineExceeded}, t.TempDir(), "schemii-phone.tail123.ts.net", roots); err == nil {
		t.Fatal("issuance failure accepted")
	}
}

func TestManagedCertificateModesAreNarrowAndPrivate(t *testing.T) {
	directory := t.TempDir()
	certs := filepath.Join(directory, "certs")
	os.Mkdir(certs, 0700)
	host := "schemii-phone.tail123.ts.net"
	managed := []string{filepath.Join(directory, "tailscaled.state"), filepath.Join(certs, "acme-account.key.pem"), filepath.Join(certs, host+".crt"), filepath.Join(certs, host+".key")}
	for _, path := range managed {
		if err := os.WriteFile(path, []byte("unit fixture"), 0644); err != nil {
			t.Fatal(err)
		}
		os.Chmod(path, 0644)
	}
	unrelated := filepath.Join(certs, "unrelated.txt")
	os.WriteFile(unrelated, []byte("preserve"), 0644)
	os.Chmod(unrelated, 0644)
	if err := protectManagedState(directory, host); err != nil {
		t.Fatal(err)
	}
	for _, path := range managed {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatal("managed file was not protected")
		}
	}
	if info, _ := os.Stat(unrelated); info.Mode().Perm() != 0644 {
		t.Fatal("unrelated file mode changed")
	}
	os.Remove(managed[2])
	os.Symlink(unrelated, managed[2])
	if err := protectManagedState(directory, host); err == nil {
		t.Fatal("symlinked managed certificate accepted")
	}
	if info, _ := os.Stat(unrelated); info.Mode().Perm() != 0644 {
		t.Fatal("symlink target mode changed")
	}
}

type recordingStarter struct{ calls int }

func (s *recordingStarter) Start() error { s.calls++; return nil }

type recordingCertificateClient struct {
	calls  int
	result fakeCertificateClient
}

func (c *recordingCertificateClient) CertPair(ctx context.Context, host string) ([]byte, []byte, error) {
	c.calls++
	return c.result.CertPair(ctx, host)
}

func TestRetainedLinksAreRejectedBeforeSDKStart(t *testing.T) {
	for _, relative := range []string{
		"tailscaled.state", "tailscaled.log.conf", "tailscaled.log1.txt", "tailscaled.log2.txt",
		"certs/acme-account.key.pem", "certs/previous.tail123.ts.net.crt", "certs/previous.tail123.ts.net.key",
	} {
		t.Run(relative, func(t *testing.T) {
			directory := t.TempDir()
			os.Mkdir(filepath.Join(directory, "certs"), 0700)
			victim := filepath.Join(t.TempDir(), "other-identity")
			os.WriteFile(victim, []byte("preserve different identity"), 0600)
			if err := os.Symlink(victim, filepath.Join(directory, filepath.FromSlash(relative))); err != nil {
				t.Fatal(err)
			}
			starter := &recordingStarter{}
			if err := startNode(starter, directory); err == nil {
				t.Fatal("unsafe retained path accepted")
			}
			if starter.calls != 0 {
				t.Fatal("SDK Start reached before retained-state guard")
			}
			if data, _ := os.ReadFile(victim); string(data) != "preserve different identity" {
				t.Fatal("different identity changed")
			}
		})
	}
	t.Run("hardlinked_state", func(t *testing.T) {
		directory := t.TempDir()
		victim := filepath.Join(t.TempDir(), "other-identity")
		os.WriteFile(victim, []byte("preserve"), 0600)
		if err := os.Link(victim, filepath.Join(directory, "tailscaled.state")); err != nil {
			t.Fatal(err)
		}
		starter := &recordingStarter{}
		if err := startNode(starter, directory); err == nil || starter.calls != 0 {
			t.Fatal("hardlinked identity reached SDK")
		}
	})
	t.Run("certificate_directory", func(t *testing.T) {
		directory := t.TempDir()
		if err := os.Symlink(t.TempDir(), filepath.Join(directory, "certs")); err != nil {
			t.Fatal(err)
		}
		starter := &recordingStarter{}
		if err := startNode(starter, directory); err == nil || starter.calls != 0 {
			t.Fatal("symlinked certificate directory reached SDK")
		}
	})
	t.Run("unsupported_certificate_name", func(t *testing.T) {
		directory := t.TempDir()
		certs := filepath.Join(directory, "certs")
		os.Mkdir(certs, 0700)
		path := filepath.Join(certs, "other.example.crt")
		os.WriteFile(path, []byte("preserve unsupported certificate"), 0600)
		starter := &recordingStarter{}
		if err := startNode(starter, directory); err == nil || starter.calls != 0 {
			t.Fatal("unsupported certificate name reached SDK")
		}
		if data, _ := os.ReadFile(path); string(data) != "preserve unsupported certificate" {
			t.Fatal("unsupported certificate changed")
		}
	})
}

func TestActualHostCacheIsGuardedBeforeCertPair(t *testing.T) {
	host := "schemii-phone.tail123.ts.net"
	for _, name := range []string{"acme-account.key.pem", host + ".crt", host + ".key"} {
		t.Run(name, func(t *testing.T) {
			directory := t.TempDir()
			certs := filepath.Join(directory, "certs")
			os.Mkdir(certs, 0700)
			victim := filepath.Join(t.TempDir(), "other-key")
			os.WriteFile(victim, []byte("preserve"), 0600)
			os.Symlink(victim, filepath.Join(certs, name))
			client := &recordingCertificateClient{}
			if err := verifyPublicCertificate(context.Background(), client, directory, host, nil); err == nil {
				t.Fatal("unsafe cache accepted")
			}
			if client.calls != 0 {
				t.Fatal("CertPair reached before cached-key guard")
			}
		})
	}
}

func TestValidRetainedIdentityAndCertificatesRemainReusable(t *testing.T) {
	directory := t.TempDir()
	certs := filepath.Join(directory, "certs")
	os.Mkdir(certs, 0700)
	host := "schemii-phone.tail123.ts.net"
	a := authority(t)
	clientResult := certificateClientFor(t, a, host)
	files := map[string][]byte{"tailscaled.state": []byte("unit identity fixture"), "certs/acme-account.key.pem": []byte("unit account fixture"), "certs/" + host + ".crt": clientResult.cert, "certs/" + host + ".key": clientResult.key}
	for name, data := range files {
		if err := os.WriteFile(filepath.Join(directory, filepath.FromSlash(name)), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	starter := &recordingStarter{}
	if err := startNode(starter, directory); err != nil || starter.calls != 1 {
		t.Fatalf("valid retained identity rejected: %v", err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(a.cert)
	client := &recordingCertificateClient{result: clientResult}
	if err := verifyPublicCertificate(context.Background(), client, directory, host, roots); err != nil || client.calls != 1 {
		t.Fatalf("valid certificate cache rejected: %v", err)
	}
	for name, expected := range files {
		actual, err := os.ReadFile(filepath.Join(directory, filepath.FromSlash(name)))
		if err != nil || string(actual) != string(expected) {
			t.Fatal("retained private bytes changed")
		}
	}
}
