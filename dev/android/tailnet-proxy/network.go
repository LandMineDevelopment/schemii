package main

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"time"

	"tailscale.com/net/dnscache"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
)

const androidDNSServer = "100.100.100.100:53"

func validateNativeTermux(prefix string, uid int, rootsFile string) error {
	if (prefix != "/data/data/com.termux/files/usr" && prefix != "/data/user/0/com.termux/files/usr") || uid < 10000 {
		return errors.New("run the tailnet helper natively in Termux, outside PRoot")
	}
	if rootsFile != filepath.Join(prefix, "etc/tls/cert.pem") {
		return errors.New("SSL_CERT_FILE must use Termux's maintained public CA bundle")
	}
	return nil
}

func vpnResolver(server string) (*net.Resolver, error) {
	if server != androidDNSServer {
		return nil, errors.New("dns-server must use the connected Android Tailscale VPN resolver on port 53")
	}
	return resolverTo(server, (&net.Dialer{Timeout: 5 * time.Second}).DialContext), nil
}

func resolverTo(server string, dial func(context.Context, string, string) (net.Conn, error)) *net.Resolver {
	return &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		if network != "udp" && network != "tcp" {
			return nil, errors.New("unsupported DNS transport")
		}
		return dial(ctx, network, server)
	}}
}

func installNetworkAdapters(resolver *net.Resolver, getter func() ([]netmon.Interface, error)) {
	// Keep the existing resolver pointer for consumers that captured it at
	// package initialization, then align the SDK's separately initialized cache.
	net.DefaultResolver.PreferGo = resolver.PreferGo
	net.DefaultResolver.StrictErrors = resolver.StrictErrors
	net.DefaultResolver.Dial = resolver.Dial
	dnscache.Get().Forward = net.DefaultResolver
	netmon.RegisterInterfaceGetter(getter)
	netns.SetEnabled(false)
}

func configureAndroidNetwork(server string) error {
	if err := validateNativeTermux(os.Getenv("PREFIX"), os.Getuid(), os.Getenv("SSL_CERT_FILE")); err != nil {
		return err
	}
	resolver, err := vpnResolver(server)
	if err != nil {
		return err
	}
	if _, err := androidIPv4Interfaces(); err != nil {
		return err
	}
	// The SDK captures dnscache.Get().Forward into control/log clients during
	// Start, so both process-local resolvers must be installed before any SDK
	// initialization. No Android/Termux resolver configuration file is changed.
	// This embedded node has no host TUN or policy routes. Leave real socket
	// routing to Android instead of requiring privileged Linux bypass marks or
	// binding to a guessed default interface after Android denies route dumps.
	installNetworkAdapters(resolver, androidIPv4Interfaces)
	return nil
}
