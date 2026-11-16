package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"sync"
	"time"

	"tailscale.com/net/dnscache"
	"tailscale.com/net/dnsfallback"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
)

// No ambient HTTP client, proxy, TLS dialer or redirect is inherited. All ACME
// requests (including server-provided URLs) stay on the directory's HTTPS origin.
// Android installs mandatory netns ownership before constructing this client.
func certificateHTTPClient(parent context.Context, directory string, roots *x509.CertPool, monitor *netmon.Monitor, current func() error) (*http.Client, func(), error) {
	u, err := url.Parse(directory)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Fragment != "" || roots == nil || monitor == nil || current == nil {
		return nil, nil, certificateError("invalid Android certificate transport")
	}
	quiet := func(string, ...any) {}
	dialer := netns.FromDialer(quiet, monitor, &net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second})
	resolver := &dnscache.Resolver{Forward: net.DefaultResolver, UseLastGood: true,
		LookupIPFallback: dnsfallback.MakeLookupFunc(quiet, monitor)}
	tr := &http.Transport{
		DialContext:       dnscache.Dialer(dialer.DialContext, resolver),
		TLSClientConfig:   &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12},
		ForceAttemptHTTP2: true, MaxIdleConns: 2, MaxIdleConnsPerHost: 2,
		IdleConnTimeout: 30 * time.Second, TLSHandshakeTimeout: 10 * time.Second,
		ResponseHeaderTimeout: 10 * time.Second, MaxResponseHeaderBytes: 1 << 20,
	}
	transport := &certificateTransport{parent: parent, origin: u.Host, transport: tr, current: current}
	client := &http.Client{Transport: transport, Timeout: 30 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("ACME redirects are disabled") }}
	return client, tr.CloseIdleConnections, nil
}

type certificateTransport struct {
	parent    context.Context
	origin    string
	transport *http.Transport
	current   func() error
}

func (t *certificateTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL.Scheme != "https" || request.URL.Host != t.origin || request.URL.User != nil || request.URL.Fragment != "" {
		return nil, certificateError("ACME endpoint is outside its approved HTTPS origin")
	}
	if err := t.current(); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(request.Context())
	stop := context.AfterFunc(t.parent, cancel)
	cleanup := func() { stop(); cancel() }
	if t.parent.Err() != nil {
		cleanup()
		return nil, t.parent.Err()
	}
	response, err := t.transport.RoundTrip(request.Clone(ctx))
	if err != nil {
		cleanup()
		return nil, err
	}
	if err = t.current(); err != nil {
		response.Body.Close()
		cleanup()
		return nil, err
	}
	// Keep cancellation attached through response-body consumption, not merely
	// until headers arrive. ACME's decoder owns and closes this body.
	response.Body = &certificateBody{ReadCloser: response.Body, cleanup: cleanup}
	return response, nil
}

type certificateBody struct {
	io.ReadCloser
	once    sync.Once
	cleanup func()
}

func (b *certificateBody) Close() error {
	err := b.ReadCloser.Close()
	b.once.Do(b.cleanup)
	return err
}
