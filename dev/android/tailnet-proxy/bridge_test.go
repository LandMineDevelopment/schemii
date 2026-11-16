package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"tailscale.com/ipn/ipnstate"
)

func TestFrameworkResolverUsesWireBridgeAndPreservesDNSAnswer(t *testing.T) {
	calls := 0
	resolver := bridgeResolver(func(ctx context.Context, query []byte) ([]byte, error) {
		calls++
		var request dnsmessage.Message
		if err := request.Unpack(query); err != nil {
			t.Fatal(err)
		}
		if len(request.Questions) != 1 || request.Questions[0].Name.String() != "bootstrap.invalid." {
			t.Fatal("query changed")
		}
		response := dnsmessage.Message{Header: dnsmessage.Header{ID: request.Header.ID, Response: true, RecursionAvailable: true}, Questions: request.Questions}
		response.Answers = []dnsmessage.Resource{{Header: dnsmessage.ResourceHeader{Name: request.Questions[0].Name, Type: dnsmessage.TypeA, Class: dnsmessage.ClassINET, TTL: 10}, Body: &dnsmessage.AResource{A: [4]byte{192, 0, 2, 9}}}}
		return response.Pack()
	})
	ips, err := resolver.LookupIP(context.Background(), "ip4", "bootstrap.invalid")
	if err != nil || len(ips) != 1 || ips[0].String() != "192.0.2.9" || calls != 1 {
		t.Fatalf("wire bridge: %v %v calls=%d", ips, err, calls)
	}
}

func TestFrameworkResolverCancellationClosesPendingExchange(t *testing.T) {
	entered, cancelled := make(chan struct{}), make(chan struct{})
	resolver := bridgeResolver(func(ctx context.Context, query []byte) ([]byte, error) {
		close(entered)
		<-ctx.Done()
		close(cancelled)
		return nil, ctx.Err()
	})
	conn, err := resolver.Dial(context.Background(), "udp", "ignored.invalid:53")
	if err != nil {
		t.Fatal(err)
	}
	query := make([]byte, 14)
	binary.BigEndian.PutUint16(query[:2], 12)
	if _, err := conn.Write(query); err != nil {
		t.Fatal(err)
	}
	<-entered
	conn.Close()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("Close left platform DNS running")
	}
	if _, err := resolver.Dial(context.Background(), "unix", "ignored"); err == nil {
		t.Fatal("non-DNS transport accepted")
	}
}

func TestFrameworkResolverRejectsMismatchedWireIdentity(t *testing.T) {
	resolver := bridgeResolver(func(ctx context.Context, query []byte) ([]byte, error) {
		out := append([]byte(nil), query...)
		out[0] ^= 1
		return out, nil
	})
	conn, err := resolver.Dial(context.Background(), "tcp", "ignored")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	query := make([]byte, 14)
	binary.BigEndian.PutUint16(query[:2], 12)
	if _, err := conn.Write(query); err != nil {
		t.Fatal(err)
	}
	if _, err := io.ReadAll(conn); err != nil {
		t.Fatal(err)
	}
}

func TestFrameworkIPv6OnlyObservationAndNoFabricatedMetadata(t *testing.T) {
	snapshot := networkSnapshot{Generation: 7, Handle: 91, Available: true, Interface: "wlan0", Index: 3, MTU: 1500, Flags: uint(net.FlagUp | net.FlagMulticast), Addresses: []string{"2001:db8::42/64"}}
	list, err := snapshot.interfaces()
	if err != nil || len(list) != 1 {
		t.Fatalf("IPv6-only observation rejected: %v", err)
	}
	addresses, err := list[0].Addrs()
	if err != nil || len(addresses) != 1 || addresses[0].String() != "2001:db8::42/64" {
		t.Fatal("actual IPv6 address/prefix changed")
	}
	for _, change := range []func(*networkSnapshot){
		func(s *networkSnapshot) { s.Index = 0 }, func(s *networkSnapshot) { s.MTU = 0 },
		func(s *networkSnapshot) { s.Available = false }, func(s *networkSnapshot) { s.Generation = 0 },
		func(s *networkSnapshot) { s.Flags = uint(net.FlagLoopback) }, func(s *networkSnapshot) { s.Addresses = []string{"not-an-address"} },
	} {
		copy := snapshot
		change(&copy)
		if _, err := copy.interfaces(); err == nil {
			t.Fatal("incomplete network snapshot accepted")
		}
	}
}

func readyStore(t *testing.T) *statusStore {
	t.Helper()
	store := newStatusStore(filepath.Join(t.TempDir(), "tailnet.json"))
	if err := store.update(func(s *status) {
		s.Listening = true
		s.Online = true
		s.CertificateReady = true
		s.CertificateExpiresAt = time.Now().Add(time.Hour).Unix()
	}); err != nil {
		t.Fatal(err)
	}
	return store
}
func TestReadinessExpiresCertificateAndFencesNetworkGeneration(t *testing.T) {
	store := readyStore(t)
	if !store.value.Ready {
		t.Fatal("valid certificate not ready")
	}
	if err := store.update(func(s *status) { s.CertificateExpiresAt = time.Now().Add(-time.Second).Unix() }); err != nil {
		t.Fatal(err)
	}
	if store.value.Ready || store.value.CertificateReady {
		t.Fatal("expired certificate remained ready")
	}
	if err := store.update(func(s *status) {
		s.CertificateReady = true
		s.CertificateExpiresAt = time.Now().Add(time.Hour).Unix()
		s.NetworkRequired = true
		s.NetworkGeneration = 8
		s.VerifiedNetworkGeneration = 7
		s.NetworkAvailable = true
	}); err != nil {
		t.Fatal(err)
	}
	if store.value.Ready {
		t.Fatal("old network proof remained ready")
	}
	if err := store.update(func(s *status) { s.VerifiedNetworkGeneration = 8 }); err != nil {
		t.Fatal(err)
	}
	if !store.value.Ready {
		t.Fatal("current generation not ready")
	}
	if err := store.update(func(s *status) { s.NetworkGeneration = 0; s.NetworkAvailable = false }); err != nil {
		t.Fatal(err)
	}
	if store.value.Ready {
		t.Fatal("failed network observation bypassed readiness")
	}
}
func TestReadinessRefusesAuthenticatedOriginChangeAndOldProbe(t *testing.T) {
	store := readyStore(t)
	host := "phone.tail123.ts.net"
	store.pinIdentity(host)
	st := &ipnstate.Status{BackendState: "Running", Self: &ipnstate.PeerStatus{DNSName: host + "."}}
	if err := store.observeNode(st, 9, true); err != nil {
		t.Fatal(err)
	}
	if err := store.observeNode(st, 8, true); err != nil {
		t.Fatal(err)
	}
	if store.value.NetworkGeneration != 9 {
		t.Fatal("stale observation replaced current network generation")
	}
	st.Self.DNSName = "changed.tail123.ts.net."
	if err := store.observeNode(st, 9, true); err == nil || store.value.Ready {
		t.Fatal("origin changed without fencing old listener")
	}
	if store.value.Hostname != "phone.tail123.ts.net" {
		t.Fatal("origin changed without host reconfiguration")
	}
}
func TestPublicRootBundleRefusesLinksAndRemainsPrivate(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "public.pem")
	if err := writePublicRoots(path, []byte("public fixture")); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("root bundle permissions")
	}
	target := filepath.Join(root, "other")
	if err := os.Link(path, target); err != nil {
		t.Fatal(err)
	}
	if err := writePublicRoots(path, []byte("changed")); err == nil {
		t.Fatal("hardlink destination accepted")
	}
	raw, _ := os.ReadFile(target)
	if string(raw) != "public fixture" {
		t.Fatal("unowned link changed")
	}
	var snapshot networkSnapshot
	if err := json.Unmarshal([]byte(`{"generation":1,"available":false}`), &snapshot); err != nil {
		t.Fatal(err)
	}
	if _, err := snapshot.interfaces(); err == nil {
		t.Fatal("unavailable snapshot fabricated an interface")
	}
}

// Both new handshakes and existing HTTP connections must stop forwarding when
// framework policy withdraws the current network, including VPN activation.
type ingressGate struct {
	networkPlatform
	allowed atomic.Bool
}

func (g *ingressGate) ingressAvailable() bool { return g.allowed.Load() }
func TestPlatformGateStopsForwardingWhileNetworkIsUnavailable(t *testing.T) {
	gate := &ingressGate{networkPlatform: termuxPlatform{}}
	calls := 0
	handler := platformHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(http.StatusNoContent) }), gate)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("POST", "https://phone.tail123.ts.net/mutate", nil))
	if response.Code != http.StatusServiceUnavailable || calls != 0 {
		t.Fatal("unverified network forwarded mutation")
	}
	gate.allowed.Store(true)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "https://phone.tail123.ts.net/", nil))
	if response.Code != http.StatusNoContent || calls != 1 {
		t.Fatal("verified network did not recover")
	}
}
