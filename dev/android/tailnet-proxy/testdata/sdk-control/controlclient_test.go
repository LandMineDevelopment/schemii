package controlclient

import (
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"tailscale.com/net/dnscache"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
	"tailscale.com/net/tsdial"
	"tailscale.com/types/key"
	"tailscale.com/util/eventbus"
)

func TestSchemiiActualControlClientBootstrap(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			blocked := errors.New("fixture stops SDK before connect")
			f := netns.SchemiiInstallControl(flag, func() error { return blocked })
			defer f.Close()
			monitor := netmon.NewStatic()
			defer monitor.Close()
			dialer := tsdial.NewDialer(monitor)
			defer dialer.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			c, err := dialer.SystemDial(ctx, "tcp4", "127.0.0.1:1")
			if c != nil {
				c.Close()
			}
			if !errors.Is(err, blocked) {
				t.Fatal("unsafe bootstrap fixture: mandatory hook bypassed", err)
			}
			before := f.Calls()
			original := dnscache.Get().Forward
			defer func() { dnscache.Get().Forward = original }()
			dnscache.Get().Forward = &net.Resolver{PreferGo: true, Dial: func(context.Context, string, string) (net.Conn, error) {
				// Go may ask A and AAAA concurrently; only the result matters.
				return nil, errors.New("fixture Android DNS failure")
			}}
			bus := eventbus.New()
			dialer.SetBus(bus)
			client, err := NewDirect(Options{
				ServerURL: "https://schemii-sdk-control.invalid", Dialer: dialer, Bus: bus,
				GetMachinePrivateKey: func() (key.MachinePrivate, error) { t.Fatal("unexpected enrollment"); return key.MachinePrivate{}, nil },
				Logf:                 func(string, ...any) {},
			})
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			if client.dnsCache.Forward != dnscache.Get().Forward || client.netMon != monitor {
				t.Fatal("constructor lost Android forwarder/monitor")
			}
			_, _, _, err = client.dnsCache.LookupIP(ctx, "schemii-sdk-control.invalid")
			if err == nil || f.Calls() <= before || f.Owned() == 0 {
				t.Fatal("actual NewDirect resolver did not bootstrap through owned SDK socket", err)
			}
			before = f.Calls()
			if err := f.Revoke(); err != nil {
				t.Fatal(err)
			}
			_, _, _, err = client.dnsCache.LookupIP(ctx, "after-revoke.invalid")
			if err == nil || f.Calls() != before || f.Owned() != 0 {
				t.Fatal("actual control resolver survived epoch revocation")
			}
		})
	}
}
