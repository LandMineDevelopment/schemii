//go:build android && cgo

package main

/*
#include "../../../platforms/android/native/network/bridge.h"
*/
import "C"

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"

	"tailscale.com/ipn"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
	"tailscale.com/tsd"
	"tailscale.com/tsnet"
)

// A c-shared library never runs main. Lifecycle belongs to NetworkBridge's host.
func main()                            {}
func defaultPlatform() networkPlatform { return &apkPlatform{} }

type apkPlatform struct {
	config             config
	publicRoots        *x509.CertPool
	rootDigest         string
	mu                 sync.Mutex
	server             *tsnet.Server // Published only after Start has finished.
	verifiedGeneration uint64
	verifiedAt         time.Time
	store              *statusStore
}

var installedAPK atomic.Pointer[apkPlatform]
var dnsRequest atomic.Uint64
var apkSession struct {
	sync.Mutex
	cancel      context.CancelFunc
	done        chan struct{}
	uncontained bool
}

func androidSnapshot() (networkSnapshot, error) {
	raw := C.schemii_android_snapshot()
	if raw == nil {
		return networkSnapshot{}, errors.New("Android network bridge observation failed")
	}
	defer C.free(unsafe.Pointer(raw))
	var snapshot networkSnapshot
	if err := json.Unmarshal([]byte(C.GoString(raw)), &snapshot); err != nil {
		return snapshot, errors.New("invalid Android network snapshot")
	}
	return snapshot, nil
}

func (p *apkPlatform) validate(c config) error {
	// This port is allocated by runtime264/runtime-interface.json. No alternate
	// port or LAN address is selected on conflicts. Termux retains port 8001.
	if c.Upstream != "https://127.0.0.1:18443" || c.DNSServer != "" {
		return errors.New("APK requires host-allocated loopback HTTPS ingress18443 and framework DNS")
	}
	return nil
}
func (p *apkPlatform) observation() string   { return "android-framework-network-v1" }
func (p *apkPlatform) roots() *x509.CertPool { return p.publicRoots }
func (p *apkPlatform) networkState(ctx context.Context) (uint64, bool) {
	snapshot, err := androidSnapshot()
	if err != nil || !snapshot.Available {
		return snapshot.Generation, false
	}
	p.mu.Lock()
	server := p.server
	already := p.verifiedGeneration == snapshot.Generation && time.Since(p.verifiedAt) < 30*time.Second
	p.mu.Unlock()
	if already {
		return snapshot.Generation, true
	}
	if server == nil {
		return snapshot.Generation, false
	}
	if _, err := proveUnderlay(ctx, p.publicRoots, server.Sys().NetMon.Get()); err != nil {
		return snapshot.Generation, false
	}
	current, err := androidSnapshot()
	if err != nil || !current.Available || current.Generation != snapshot.Generation {
		return snapshot.Generation, false
	}
	p.mu.Lock()
	p.verifiedGeneration, p.verifiedAt = snapshot.Generation, time.Now()
	p.mu.Unlock()
	return snapshot.Generation, true
}

func (p *apkPlatform) ingressAvailable() bool {
	snapshot, err := androidSnapshot()
	p.mu.Lock()
	defer p.mu.Unlock()
	return err == nil && snapshot.Available && snapshot.Generation == p.verifiedGeneration && time.Since(p.verifiedAt) < 30*time.Second
}

func apkInterfaces() ([]netmon.Interface, error) {
	snapshot, err := androidSnapshot()
	if err != nil {
		return nil, err
	}
	return snapshot.interfaces()
}
func bindAndroidSocket(fd int) error {
	snapshot, err := androidSnapshot()
	if err != nil || !snapshot.Available {
		return errors.New("no usable Android network")
	}
	if C.schemii_android_bind(C.uint64_t(snapshot.Generation), C.int(fd)) != 1 {
		return errors.New("Android socket binding denied or network generation changed")
	}
	return nil
}
func androidDNS(ctx context.Context, query []byte) ([]byte, error) {
	snapshot, err := androidSnapshot()
	if err != nil || !snapshot.Available {
		return nil, errors.New("Android DNS network unavailable")
	}
	request := dnsRequest.Add(1)
	timeout := 10 * time.Second
	if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) < timeout {
		timeout = time.Until(deadline)
	}
	if timeout < time.Millisecond || ctx.Err() != nil {
		return nil, context.DeadlineExceeded
	}
	if C.schemii_android_prepare_dns(C.uint64_t(request)) != 1 {
		return nil, errors.New("Android DNS request unavailable")
	}
	cancelled := context.AfterFunc(ctx, func() { C.schemii_android_cancel_dns(C.uint64_t(request)) })
	defer cancelled()
	var length C.int
	answer := C.schemii_android_dns(C.uint64_t(snapshot.Generation), C.uint64_t(request),
		(*C.uchar)(unsafe.Pointer(&query[0])), C.int(len(query)), C.int(timeout.Milliseconds()), &length)
	if answer == nil {
		return nil, errors.New("Android DNS query failed or cancelled")
	}
	defer C.free(unsafe.Pointer(answer))
	current, err := androidSnapshot()
	if err != nil || current.Generation != snapshot.Generation || !current.Available || ctx.Err() != nil {
		return nil, errors.New("Android DNS result belongs to an unavailable generation")
	}
	return C.GoBytes(unsafe.Pointer(answer), length), nil
}

func (p *apkPlatform) install() error {
	if _, err := apkInterfaces(); err != nil {
		return err
	}
	pem := C.schemii_android_roots()
	if pem == nil {
		return errors.New("Android public system roots unavailable")
	}
	defer C.free(unsafe.Pointer(pem))
	bytes := []byte(C.GoString(pem))
	p.publicRoots = x509.NewCertPool()
	if !p.publicRoots.AppendCertsFromPEM(bytes) {
		return errors.New("Android system roots are not valid PEM")
	}
	sum := sha256.Sum256(bytes)
	p.rootDigest = hex.EncodeToString(sum[:])
	if p.config.StatusFile != "" {
		// SDK control/ACME use the same public roots, not the backend CA.
		// This dedicated process must not initialize system trust before here.
		path := filepath.Join(filepath.Dir(p.config.StatusFile), "android-public-roots.pem")
		if err := writePublicRoots(path, bytes); err != nil {
			return err
		}
		if err := os.Setenv("SSL_CERT_FILE", path); err != nil {
			return err
		}
		if err := os.Setenv("SSL_CERT_DIR", ""); err != nil {
			return err
		}
	}
	installResolver(bridgeResolver(androidDNS), apkInterfaces)
	netns.SetAndroidProtectFunc(nil) // This app neither owns nor requests a VPN.
	netns.SetAndroidBindToNetworkFunc(bindAndroidSocket)
	netns.SetEnabled(true)
	installedAPK.Store(p)
	return nil
}
func (p *apkPlatform) configure(*tsnet.Server) error { return p.install() }
func (p *apkPlatform) started(server *tsnet.Server, store *statusStore) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.server, p.store = server, store
	p.changedLocked()
}
func (p *apkPlatform) stopped() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.server, p.store = nil, nil
}
func (p *apkPlatform) changedLocked() {
	snapshot, err := androidSnapshot()
	p.verifiedGeneration = 0
	if p.store != nil {
		_ = p.store.update(func(s *status) {
			s.NetworkRequired = true
			if snapshot.Generation >= s.NetworkGeneration {
				s.NetworkGeneration = snapshot.Generation
			}
			s.NetworkAvailable = err == nil && snapshot.Available
			// Only a subsequent observation can certify this generation.
			s.VerifiedNetworkGeneration = 0
		})
	}
	if p.server != nil {
		netmon.UpdateLastKnownDefaultRouteInterface(snapshot.Interface)
		netmon.UpdateLastKnownDefaultGateway(snapshot.Gateway)
		// Publicly exposed but explicitly unstable SDK API: pinned and isolated.
		p.server.Sys().NetMon.Get().InjectEvent()
	}
}

//export SchemiiNetworkChanged
func SchemiiNetworkChanged() {
	if p := installedAPK.Load(); p != nil {
		p.mu.Lock()
		defer p.mu.Unlock()
		p.changedLocked()
	}
}

func resultJSON(value any) *C.char {
	raw, err := json.Marshal(value)
	if err != nil {
		raw = []byte(`{"error":"cannot encode native result","ready":false}`)
	}
	return C.CString(string(raw))
}
func beginAPK() (context.Context, chan struct{}, error) {
	apkSession.Lock()
	defer apkSession.Unlock()
	if apkSession.uncontained {
		return nil, nil, errors.New("incomplete SDK initialization requires host process containment")
	}
	if apkSession.done != nil {
		select {
		case <-apkSession.done:
		default:
			return nil, nil, errors.New("native network operation already active")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	apkSession.cancel, apkSession.done = cancel, done
	return ctx, done, nil
}

//export SchemiiNetworkProbe
func SchemiiNetworkProbe() *C.char {
	ctx, done, err := beginAPK()
	if err != nil {
		return resultJSON(map[string]any{"error": err.Error(), "ready": false})
	}
	defer close(done)
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	p := &apkPlatform{}
	out := map[string]any{"schema": 1, "probe": "android-network-v1", "ready": false, "stack_acceptance": false, "tailscale_version": tailscaleVersion}
	if err := p.install(); err != nil {
		out["error"] = err.Error()
		return resultJSON(out)
	}
	snapshot, _ := androidSnapshot()
	out["generation"], out["network_handle"], out["interfaces"], out["private_dns"] = snapshot.Generation, snapshot.Handle, snapshot.Addresses, snapshot.PrivateDNS
	out["native_bridge_sha256"] = C.GoString(C.schemii_android_bridge_identity())
	out["public_roots_sha256"], out["public_root_count"] = p.rootDigest, len(p.publicRoots.Subjects())
	sys := tsd.NewSystem()
	monitor, err := netmon.New(sys.Bus.Get(), func(string, ...any) {})
	if err != nil {
		out["error"] = "SDK network monitor failed"
		return resultJSON(out)
	}
	defer monitor.Close()
	if monitor.InterfaceState() == nil {
		out["error"] = "SDK interface observation failed"
		return resultJSON(out)
	}
	out["sdk_interfaces_ok"] = true
	proof, err := proveUnderlay(ctx, p.publicRoots, monitor)
	for name, value := range proof {
		out[name] = value
	}
	if err != nil {
		out["error"] = err.Error()
		return resultJSON(out)
	}
	current, err := androidSnapshot()
	out["ready"] = err == nil && current.Available && current.Generation == snapshot.Generation
	if out["ready"] != true {
		out["error"] = "network generation changed during probe"
	}
	return resultJSON(out)
}

//export SchemiiNetworkStart
func SchemiiNetworkStart(configuration *C.char) *C.char {
	var cfg config
	decoder := json.NewDecoder(strings.NewReader(C.GoString(configuration)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&cfg); err != nil {
		return resultJSON(map[string]any{"error": "invalid native node configuration", "started": false})
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return resultJSON(map[string]any{"error": "native configuration must be one JSON object", "started": false})
	}
	cfg.instanceID = rand.Text()
	p := &apkPlatform{config: cfg}
	cfg.platform = p
	if _, err := cfg.validate(); err != nil {
		return resultJSON(map[string]any{"error": err.Error(), "started": false})
	}
	ctx, done, err := beginAPK()
	if err != nil {
		return resultJSON(map[string]any{"error": err.Error(), "started": false})
	}
	go func() {
		defer close(done)
		err := run(ctx, cfg)
		if errors.Is(err, errInitializationIncomplete) {
			apkSession.Lock()
			apkSession.uncontained = true
			apkSession.Unlock()
		}
	}()
	return resultJSON(map[string]any{"started": true, "instance_id": cfg.instanceID, "ready": false})
}

//export SchemiiNetworkStop
func SchemiiNetworkStop(timeoutMS C.int) C.int {
	if timeoutMS < 1 || timeoutMS > 15000 {
		return 0
	}
	apkSession.Lock()
	cancel, done, uncontained := apkSession.cancel, apkSession.done, apkSession.uncontained
	apkSession.Unlock()
	if uncontained {
		return 0
	}
	if cancel != nil {
		cancel()
	}
	if done == nil {
		return 1
	}
	select {
	case <-done:
		apkSession.Lock()
		unsafe := apkSession.uncontained
		apkSession.Unlock()
		if unsafe {
			return 0
		}
		return 1
	case <-time.After(time.Duration(timeoutMS) * time.Millisecond):
		return 0
	}
}

// Compile-time checks keep both consumers on the same platform/node boundary.
var _ networkPlatform = (*apkPlatform)(nil)

// This is an underlay proof, not a tailnet-node or application readiness probe.
func proveUnderlay(ctx context.Context, roots *x509.CertPool, monitor *netmon.Monitor) (map[string]any, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	out := map[string]any{}
	control, _ := url.Parse(ipn.DefaultControlURL)
	addresses, err := net.DefaultResolver.LookupIPAddr(ctx, control.Hostname())
	if err != nil || len(addresses) == 0 {
		return out, errors.New("framework DNS failed")
	}
	out["dns_ok"] = true
	dialer := netns.NewDialer(func(string, ...any) {}, monitor)
	target := net.JoinHostPort(control.Hostname(), "443")
	udp, err := dialer.DialContext(ctx, "udp", target)
	if err != nil {
		return out, errors.New("Android UDP socket binding failed")
	}
	udp.Close()
	out["udp_socket_bound"] = true
	raw, err := dialer.DialContext(ctx, "tcp", target)
	if err != nil {
		return out, errors.New("Android TCP socket binding failed")
	}
	defer raw.Close()
	out["tcp_socket_bound"] = true
	secured := tls.Client(raw, &tls.Config{ServerName: control.Hostname(), RootCAs: roots, MinVersion: tls.VersionTLS12})
	if err := secured.HandshakeContext(ctx); err != nil {
		return out, errors.New("public system-root TLS verification failed")
	}
	out["tls_ok"] = true
	out["peer_certificate_expires_at"] = secured.ConnectionState().PeerCertificates[0].NotAfter.Unix()
	return out, nil
}
