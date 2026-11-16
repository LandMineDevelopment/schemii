package controlhttp

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
)

func TestSchemiiActualControlHTTPDefaultResolver(t *testing.T) {
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
			original := dnscache.Get().Forward
			defer func() { dnscache.Get().Forward = original }()
			dnscache.Get().Forward = &net.Resolver{PreferGo: true, Dial: func(context.Context, string, string) (net.Conn, error) {
				return nil, errors.New("fixture Android DNS failure")
			}}
			// Exercise the real default resolver, with the monitor and SystemDial
			// supplied by ts2021; do not use its unsafe nil/default dialer branch.
			d := &Dialer{NetMon: monitor, Dialer: dialer.SystemDial, Logf: func(string, ...any) {}}
			before := f.Calls()
			resolver := d.resolver()
			_, _, _, err = resolver.LookupIP(ctx, "schemii-sdk-control.invalid")
			if err == nil || f.Calls() <= before || f.Owned() == 0 {
				t.Fatal("controlhttp default resolver did not bootstrap through owned SDK socket", err)
			}
			before = f.Calls()
			if err := f.Revoke(); err != nil {
				t.Fatal(err)
			}
			_, _, _, err = resolver.LookupIP(ctx, "after-revoke.invalid")
			if err == nil || f.Calls() != before || f.Owned() != 0 {
				t.Fatal("controlhttp resolver survived epoch revocation")
			}
		})
	}
}
