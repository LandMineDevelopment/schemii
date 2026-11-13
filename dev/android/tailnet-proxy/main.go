// Schemii's optional Android tailnet HTTPS listener. See README.md and issue #254.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/client/local"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

const tailscaleVersion = "v1.104.7"

type config struct {
	StateDir   string
	StatusFile string
	Hostname   string
	Upstream   string
	UpstreamCA string
	DNSServer  string
}

var nodeName = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)

func (c config) validate() (*url.URL, error) {
	if _, err := vpnResolver(c.DNSServer); err != nil {
		return nil, err
	}
	if !nodeName.MatchString(c.Hostname) {
		return nil, errors.New("hostname must be a lowercase DNS label of at most 63 characters")
	}
	for _, p := range []string{c.StateDir, c.StatusFile, c.UpstreamCA} {
		if !filepath.IsAbs(p) || filepath.Clean(p) != p || p == "/" {
			return nil, errors.New("state, status and CA paths must be absolute, clean file paths")
		}
	}
	if filepath.Base(c.StatusFile) != "tailnet.json" {
		return nil, errors.New("status filename must be tailnet.json")
	}
	relative, _ := filepath.Rel(c.StateDir, c.StatusFile)
	if relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return nil, errors.New("status must be outside the private node state directory")
	}
	u, err := url.Parse(c.Upstream)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.RawQuery != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("upstream must be the local HTTPS ingress without credentials, path or query")
	}
	if u.Hostname() != "127.0.0.1" || u.Port() != "8001" {
		return nil, errors.New("upstream must be https://127.0.0.1:8001")
	}
	return u, nil
}

// Do not silently consume authentication material from a surrounding shell.
// Interactive enrollment is the only supported flow for this dedicated node.
func rejectAuthEnvironment() error {
	for _, name := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TS_CLIENT_SECRET", "TS_CLIENT_ID", "TS_ID_TOKEN", "TS_AUDIENCE", "TSNET_FORCE_LOGIN", "TS_CONTROL_URL", "TS_LOGIN_SERVER", "TSNET_LOGIN_SERVER"} {
		if os.Getenv(name) != "" {
			return errors.New("remove Tailscale authentication environment overrides; use the dedicated interactive login")
		}
	}
	return nil
}

func privateDir(path string) error {
	// The launcher supplies an app-private directory. Reject symlink traversal
	// rather than redirecting node keys or status into another storage location.
	for p := path; p != "/"; p = filepath.Dir(p) {
		info, err := os.Lstat(p)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("private directory contains an invalid path component")
		}
	}
	if err := os.MkdirAll(path, 0700); err != nil {
		return errors.New("cannot create private runtime directory")
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0700 {
		return errors.New("private runtime directory must have mode 0700")
	}
	return nil
}

func protectManagedState(directory, host string) error {
	// These are read or opened by the pinned SDK during Start, before any
	// authenticated hostname is available. Logs are excluded from backup but
	// must not redirect SDK I/O into another file or identity either.
	paths := []string{
		filepath.Join(directory, "tailscaled.state"),
		filepath.Join(directory, "tailscaled.log.conf"),
		filepath.Join(directory, "tailscaled.log1.txt"),
		filepath.Join(directory, "tailscaled.log2.txt"),
	}
	certs := filepath.Join(directory, "certs")
	if info, err := os.Lstat(certs); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("private certificate directory is invalid")
		}
		if info.Mode().Perm() != 0700 {
			if err := os.Chmod(certs, 0700); err != nil {
				return errors.New("cannot protect private certificate directory")
			}
		}
		entries, err := os.ReadDir(certs)
		if err != nil {
			return errors.New("cannot inspect private certificate directory")
		}
		for _, entry := range entries {
			name := entry.Name()
			base, cert := strings.CutSuffix(name, ".crt")
			if !cert {
				base, cert = strings.CutSuffix(name, ".key")
			}
			if !cert {
				continue
			} // Unknown/temporary files are never opened.
			if !validDNSName(base) {
				return errors.New("private certificate cache has an unsupported certificate name")
			}
			paths = append(paths, filepath.Join(certs, name))
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return errors.New("cannot inspect private certificate directory")
	}
	paths = append(paths, filepath.Join(certs, "acme-account.key.pem"))
	if validDNSName(host) {
		paths = append(paths, filepath.Join(certs, host+".crt"), filepath.Join(certs, host+".key"))
	}
	for _, path := range paths {
		info, err := os.Lstat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("private node file is invalid")
		}
		if stat, ok := info.Sys().(*syscall.Stat_t); !ok || stat.Nlink != 1 {
			return errors.New("private node file must have a single link")
		}
		if info.Mode().Perm() == 0600 {
			continue
		}
		fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
		if err != nil {
			return errors.New("cannot open private node file")
		}
		file := os.NewFile(uintptr(fd), "managed-node-file")
		err = file.Chmod(0600)
		if err == nil {
			err = file.Sync()
		}
		closeErr := file.Close()
		if errors.Join(err, closeErr) != nil {
			return errors.New("cannot protect private node file")
		}
	}
	return nil
}

type nodeStarter interface{ Start() error }

func startNode(server nodeStarter, directory string) error {
	if err := protectManagedState(directory, ""); err != nil {
		return err
	}
	return server.Start()
}

type status struct {
	Schema             int      `json:"schema"`
	PID                int      `json:"pid"`
	BackendState       string   `json:"backend_state"`
	StartupStage       string   `json:"startup_stage,omitempty"`
	NetworkObservation string   `json:"network_observation,omitempty"`
	Online             bool     `json:"online"`
	Ready              bool     `json:"ready"`
	Listening          bool     `json:"listening"`
	CertificateReady   bool     `json:"certificate_ready"`
	Hostname           string   `json:"hostname"`
	Origin             string   `json:"origin"`
	TailnetIPs         []string `json:"tailnet_ips"`
	LoginURL           string   `json:"login_url,omitempty"`
	Error              string   `json:"error,omitempty"`
	UpdatedAt          int64    `json:"updated_at"`
}

type statusStore struct {
	mu        sync.Mutex
	path      string
	value     status
	lastValue string
	lastWrite time.Time
}

func newStatusStore(path string) *statusStore {
	return &statusStore{path: path, value: status{Schema: 1, PID: os.Getpid(), BackendState: "Starting", TailnetIPs: []string{}}}
}

func (s *statusStore) update(change func(*status)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	change(&s.value)
	s.value.Ready = s.value.Listening && s.value.Online && s.value.CertificateReady
	comparison := s.value
	comparison.UpdatedAt = 0
	encoded, err := json.Marshal(comparison)
	if err != nil {
		return err
	}
	if string(encoded) == s.lastValue && time.Since(s.lastWrite) < 30*time.Second {
		return nil
	}
	s.value.UpdatedAt = time.Now().Unix()
	if err := writeStatus(s.path, s.value); err != nil {
		return err
	}
	s.lastValue, s.lastWrite = string(encoded), time.Now()
	return nil
}

func lockNode(directory string) (*os.File, error) {
	fd, err := syscall.Open(filepath.Join(directory, "schemii.lock"), syscall.O_RDWR|syscall.O_CREAT|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, errors.New("cannot open the private node lock")
	}
	file := os.NewFile(uintptr(fd), "schemii.lock")
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		file.Close()
		return nil, errors.New("private node lock is not a regular file")
	}
	if err := file.Chmod(0600); err != nil {
		file.Close()
		return nil, errors.New("cannot protect the private node lock")
	}
	if err := syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		file.Close()
		return nil, errors.New("dedicated tailnet identity is already in use")
	}
	return file, nil
}

func writeStatus(path string, value status) error {
	if info, err := os.Lstat(path); err == nil && (!info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0) {
		return errors.New("status destination is not a regular file")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return errors.New("cannot inspect private status destination")
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".tailnet-status-*")
	if err != nil {
		return errors.New("cannot create private status file")
	}
	defer os.Remove(file.Name())
	if err = file.Chmod(0600); err == nil {
		err = json.NewEncoder(file).Encode(value)
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err = errors.Join(err, closeErr); err != nil {
		return errors.New("cannot persist private status file")
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return errors.New("cannot activate private status file")
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil {
		return errors.New("cannot persist private status directory")
	}
	return nil
}

func identity(st *ipnstate.Status) (string, string, []string) {
	host := ""
	if st.Self != nil {
		host = strings.TrimSuffix(st.Self.DNSName, ".")
	}
	origin := ""
	if validDNSName(host) {
		origin = "https://" + host
	}
	ips := make([]string, 0, len(st.TailscaleIPs))
	for _, ip := range st.TailscaleIPs {
		ips = append(ips, ip.String())
	}
	return host, origin, ips
}

func validDNSName(host string) bool {
	if len(host) > 253 || !strings.HasSuffix(host, ".ts.net") {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if !nodeName.MatchString(label) {
			return false
		}
	}
	return true
}

type certificateClient interface {
	CertPair(context.Context, string) ([]byte, []byte, error)
}

func verifyPublicCertificate(ctx context.Context, client certificateClient, directory, host string, roots *x509.CertPool) error {
	// CertPair reads the cached key and ACME identity before issuing anything.
	// Guard immediately before that read as well as before SDK initialization.
	if err := protectManagedState(directory, host); err != nil {
		return err
	}
	certPEM, keyPEM, err := client.CertPair(ctx, host)
	if err != nil {
		if retryAfter, limited := local.RateLimitRetryAfter(err); limited {
			return fmt.Errorf("tailnet certificate issuance is rate limited; retry after %d seconds", int64(retryAfter.Seconds())+1)
		}
		return errors.New("tailnet certificate issuance failed; check MagicDNS, HTTPS approval and internet access")
	}
	pair, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil || len(pair.Certificate) == 0 {
		return errors.New("tailnet returned an invalid certificate/key pair")
	}
	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return errors.New("tailnet returned an invalid leaf certificate")
	}
	intermediates := x509.NewCertPool()
	for _, der := range pair.Certificate[1:] {
		cert, err := x509.ParseCertificate(der)
		if err != nil {
			return errors.New("tailnet returned an invalid certificate chain")
		}
		intermediates.AddCert(cert)
	}
	// A nil Roots uses Debian's system roots. The private Schemii ingress CA
	// deliberately belongs only to the upstream transport, never this check.
	if _, err := leaf.Verify(x509.VerifyOptions{DNSName: host, Roots: roots, Intermediates: intermediates}); err != nil {
		return errors.New("tailnet certificate is not valid for its hostname and system trust roots")
	}
	return nil
}

func observe(ctx context.Context, client *local.Client, store *statusStore, failures chan<- error, protect func(string) error) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		probe, cancel := context.WithTimeout(ctx, 5*time.Second)
		st, err := client.StatusWithoutPeers(probe)
		cancel()
		if err == nil {
			host, _, _ := identity(st)
			err = protect(host)
		}
		if err == nil {
			err = store.update(func(value *status) {
				value.BackendState = st.BackendState
				value.Online = st.BackendState == "Running"
				value.Hostname, value.Origin, value.TailnetIPs = identity(st)
				value.LoginURL = st.AuthURL
			})
		}
		if err != nil && ctx.Err() == nil {
			// A lost private status path must not leave an unobservable server.
			select {
			case failures <- errors.New("tailnet status observation failed"):
			default:
			}
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func proxyHandler(target *url.URL, caPEM []byte, host string) (http.Handler, *http.Transport, error) {
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, nil, errors.New("upstream CA file does not contain a PEM certificate")
	}
	transport := &http.Transport{
		Proxy:               nil,
		DialContext:         (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSClientConfig:     &tls.Config{RootCAs: roots, ServerName: "localhost", MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: 900 * time.Second,
		IdleConnTimeout: 90 * time.Second, MaxIdleConns: 16,
	}
	proxy := &httputil.ReverseProxy{
		Transport: transport, FlushInterval: -1,
		Rewrite: func(request *httputil.ProxyRequest) {
			request.SetURL(target)
			request.Out.Host = request.In.Host
			request.SetXForwarded()
			request.Out.Header.Set("X-Forwarded-Host", request.In.Host)
			request.Out.Header.Set("X-Forwarded-Proto", "https")
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			http.Error(w, "Schemii backend is unavailable", http.StatusBadGateway)
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != host && r.Host != host+":443" {
			http.Error(w, "Invalid host header", http.StatusMisdirectedRequest)
			return
		}
		proxy.ServeHTTP(w, r)
	})
	return handler, transport, nil
}

func run(ctx context.Context, cfg config) (returned error) {
	target, err := cfg.validate()
	if err != nil {
		return err
	}
	if err = rejectAuthEnvironment(); err != nil {
		return err
	}
	if err = privateDir(cfg.StateDir); err != nil {
		return err
	}
	if err = privateDir(filepath.Dir(cfg.StatusFile)); err != nil {
		return err
	}
	lock, err := lockNode(cfg.StateDir)
	if err != nil {
		return err
	}
	defer lock.Close()
	caPEM, err := os.ReadFile(cfg.UpstreamCA)
	if err != nil {
		return errors.New("cannot read the upstream CA file")
	}
	// Validate the trust anchor before initializing a new Tailscale identity.
	if _, transport, err := proxyHandler(target, caPEM, "validation.ts.net"); err != nil {
		return err
	} else {
		transport.CloseIdleConnections()
	}
	store := newStatusStore(cfg.StatusFile)
	if err = store.update(func(*status) {}); err != nil {
		return err
	}
	managedHost := ""
	initializationFinished := true
	defer func() {
		// tsnet.Close runs before this finalizer, so certificate renewal can no
		// longer replace a protected .crt with the library's public 0644 mode.
		if initializationFinished {
			if err := protectManagedState(cfg.StateDir, managedHost); returned == nil && err != nil {
				returned = err
			}
		}
		_ = store.update(func(value *status) {
			value.Listening, value.Online, value.CertificateReady, value.BackendState, value.LoginURL = false, false, false, "Stopped", ""
			if returned != nil {
				value.BackendState, value.Error = "Failed", returned.Error()
			}
		})
	}()
	diagnostics := diagnosticLogger(os.Stderr)
	if err := store.update(func(value *status) { value.StartupStage = "SDK initialization" }); err != nil {
		return err
	}
	server := &tsnet.Server{Dir: cfg.StateDir, Hostname: cfg.Hostname,
		UserLogf: diagnostics, Logf: diagnostics}
	initializationFinished, err = initializeNode(ctx, func() error {
		if err := store.update(func(value *status) {
			value.StartupStage = "Android interface observation"
			value.NetworkObservation = interfaceObservationMode
		}); err != nil {
			return err
		}
		if err := configureAndroidNetwork(cfg.DNSServer); err != nil {
			return err
		}
		diagnostics("network observation configured: %s (IPv6-only underlay observation is unsupported)", interfaceObservationMode)
		if err := store.update(func(value *status) { value.StartupStage = "SDK initialization" }); err != nil {
			return err
		}
		return startNode(server, cfg.StateDir)
	}, 20*time.Second)
	if err != nil {
		diagnostics("initialization failed: %v", err)
		if ctx.Err() != nil {
			return nil
		}
		return errors.New("tailnet SDK initialization failed; inspect its private startup stage and bounded diagnostics")
	}
	defer server.Close()
	if ctx.Err() != nil {
		return nil
	}
	if err := store.update(func(value *status) { value.StartupStage = "waiting for tailnet login or approval" }); err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	client, err := server.LocalClient()
	if err != nil {
		return errors.New("tailnet local client failed")
	}
	failures := make(chan error, 1)
	observed := make(chan struct{})
	go func() {
		defer close(observed)
		observe(ctx, client, store, failures, func(host string) error { return protectManagedState(cfg.StateDir, host) })
	}()
	defer func() { cancel(); <-observed }()
	type onlineResult struct {
		status *ipnstate.Status
		err    error
	}
	online := make(chan onlineResult, 1)
	go func() { status, err := server.Up(ctx); online <- onlineResult{status, err} }()
	var st *ipnstate.Status
	select {
	case result := <-online:
		if result.err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return errors.New("tailnet did not become online; complete its private login or node approval")
		}
		st = result.status
	case <-ctx.Done():
		return nil
	case err = <-failures:
		return err
	}
	host, origin, ips := identity(st)
	if origin == "" {
		return errors.New("tailnet did not supply a valid ts.net hostname; enable MagicDNS")
	}
	managedHost = host
	if err = store.update(func(value *status) {
		value.BackendState, value.Online, value.Hostname, value.Origin, value.TailnetIPs = "Running", true, host, origin, ips
		value.LoginURL = ""
	}); err != nil {
		return err
	}
	if st.CurrentTailnet == nil || !st.CurrentTailnet.MagicDNSEnabled {
		return errors.New("enable MagicDNS in the tailnet before using HTTPS")
	}
	allowed := false
	for _, domain := range st.CertDomains {
		if strings.TrimSuffix(domain, ".") == host {
			allowed = true
		}
	}
	if !allowed {
		return errors.New("enable HTTPS certificates for this node in the tailnet")
	}
	issuance, finishIssuance := context.WithTimeout(ctx, 90*time.Second)
	if err := store.update(func(value *status) { value.StartupStage = "certificate issuance" }); err != nil {
		finishIssuance()
		return err
	}
	err = verifyPublicCertificate(issuance, client, cfg.StateDir, host, nil)
	finishIssuance()
	if err != nil {
		if ctx.Err() != nil {
			return nil
		}
		return err
	}
	if err := protectManagedState(cfg.StateDir, host); err != nil {
		return err
	}
	if err = store.update(func(value *status) { value.CertificateReady = true }); err != nil {
		return err
	}
	handler, transport, err := proxyHandler(target, caPEM, host)
	if err != nil {
		return err
	}
	defer transport.CloseIdleConnections()
	// The only registered listener is this tailnet TLS port. No fallback, host
	// listener, Funnel, SOCKS proxy or forwarding of unmatched ports is enabled.
	type result struct {
		listener net.Listener
		err      error
	}
	listening := make(chan result, 1)
	go func() {
		listener, err := server.ListenTLS("tcp", ":443")
		listening <- result{listener, err}
	}()
	var listener net.Listener
	select {
	case result := <-listening:
		if result.err != nil {
			return errors.New("tailnet HTTPS listener failed; enable MagicDNS and HTTPS certificates in the tailnet")
		}
		listener = result.listener
	case <-ctx.Done():
		return nil
	case err = <-failures:
		return err
	}
	defer listener.Close()
	web := &http.Server{Handler: handler, ReadHeaderTimeout: 15 * time.Second,
		IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20, ErrorLog: log.New(io.Discard, "", 0)}
	done := make(chan error, 1)
	go func() { done <- web.Serve(listener) }()
	if err = store.update(func(value *status) { value.Listening = true }); err != nil {
		web.Close()
		return err
	}
	if err := store.update(func(value *status) { value.StartupStage = "ready" }); err != nil {
		web.Close()
		return err
	}
	select {
	case err = <-done:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return errors.New("tailnet HTTPS server stopped unexpectedly")
	case err = <-failures:
	case <-ctx.Done():
	}
	shutdown, stop := context.WithTimeout(context.Background(), 10*time.Second)
	defer stop()
	if closeErr := web.Shutdown(shutdown); closeErr != nil {
		web.Close()
	}
	return err
}

func main() {
	syscall.Umask(0077)
	cfg := config{}
	flag.StringVar(&cfg.StateDir, "state-dir", "", "private persistent node directory")
	flag.StringVar(&cfg.StatusFile, "status-file", "", "private status JSON outside node directory")
	flag.StringVar(&cfg.Hostname, "hostname", "schemii-phone", "requested dedicated node hostname")
	flag.StringVar(&cfg.Upstream, "upstream", "https://127.0.0.1:8001", "existing local HTTPS ingress")
	flag.StringVar(&cfg.UpstreamCA, "upstream-ca", "", "existing local ingress CA PEM")
	flag.StringVar(&cfg.DNSServer, "dns-server", androidDNSServer, "connected Android Tailscale VPN DNS resolver")
	version := flag.Bool("version", false, "report the pinned tailnet library version without starting")
	flag.Parse()
	if *version {
		fmt.Println("schemii-tailnet-proxy tsnet " + tailscaleVersion)
		return
	}
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "Unexpected positional arguments")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := run(ctx, cfg); err != nil {
		fmt.Fprintln(os.Stderr, "Schemii tailnet: "+err.Error())
		os.Exit(1)
	}
}
