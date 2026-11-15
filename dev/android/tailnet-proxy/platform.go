package main

import (
	"context"
	"crypto/x509"
	"net"

	"tailscale.com/net/dnscache"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
	"tailscale.com/tsnet"
)

// Platform differences end here; node enrollment, identity storage, TLS and HTTP
// proxy behavior have one implementation. A process owns one platform because
// the SDK's resolver/interface/socket hooks are process-global.
type networkPlatform interface {
	validate(config) error
	configure(*tsnet.Server) error
	observation() string
	networkState(context.Context) (generation uint64, available bool)
	started(*tsnet.Server, *statusStore)
	stopped()
	ingressAvailable() bool
	roots() *x509.CertPool
}

func (c config) networkPlatform() networkPlatform {
	if c.platform != nil {
		return c.platform
	}
	return defaultPlatform()
}

func installResolver(resolver *net.Resolver, getter func() ([]netmon.Interface, error)) {
	// Preserve the pointer captured by SDK clients, before any SDK initialization.
	net.DefaultResolver.PreferGo = resolver.PreferGo
	net.DefaultResolver.StrictErrors = resolver.StrictErrors
	net.DefaultResolver.Dial = resolver.Dial
	dnscache.Get().Forward = net.DefaultResolver
	netmon.RegisterInterfaceGetter(getter)
}

func installNetworkAdapters(resolver *net.Resolver, getter func() ([]netmon.Interface, error)) {
	installResolver(resolver, getter)
	// The retained Linux/Termux consumer leaves routing to its Android VPN.
	netns.SetEnabled(false)
}
