package localapi

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"tailscale.com/client/local"
)

func TestSchemiiAndroidCertificateRouteSelection(t *testing.T) {
	// Compile the actual SDK with Android's cert.go exclusion/disabled stub.
	// The common route registry remains intact. No LocalBackend test API used.
	if _, _, ok := handlerForPath("/localapi/v0/cert/apk.tail123.ts.net"); ok {
		t.Fatal("Android unexpectedly registered cert/")
	}
	for _, path := range []string{"set-dns", "cert-domains"} {
		if _, _, ok := handlerForPath("/localapi/v0/" + path); !ok {
			t.Fatalf("Android route %s unavailable", path)
		}
	}
	fn, _, _ := handlerForPath("/localapi/v0/set-dns")
	w := httptest.NewRecorder()
	fn(&Handler{PermitWrite: true}, w, httptest.NewRequest("GET", "http://local-tailscaled.sock/localapi/v0/set-dns", nil))
	if w.Code != http.StatusBadRequest {
		t.Fatal("registered set-dns method control changed", w.Code)
	}
	cache := t.TempDir()
	for _, name := range []string{"apk.tail123.ts.net.crt", "apk.tail123.ts.net.key"} {
		if err := os.WriteFile(filepath.Join(cache, name), []byte("retained fixture"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if fn, _, ok := handlerForPath(r.URL.Path); ok {
			fn(&Handler{PermitWrite: true}, w, r)
		} else {
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client := &local.Client{UseSocketOnly: true, OmitAuth: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}}
	if _, _, err := client.CertPair(context.Background(), "apk.tail123.ts.net"); err == nil || calls != 1 {
		t.Fatal("actual old CertPair failed to reproduce Android 404", err, calls)
	}
}
