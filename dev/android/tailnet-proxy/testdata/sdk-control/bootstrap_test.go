package dnsfallback_test

import (
	"context"
	"errors"
	"fmt"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/logpolicy"
	"tailscale.com/net/dnscache"
	"tailscale.com/net/dnsfallback"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
	"tailscale.com/net/tsdial"
)

func TestSchemiiStandardBootstrapUsesOwnedSDKSocket(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			// Allow cookie registration, then reject inside the actual SDK Control
			// hook before connect. No public packet or HTTPS success is claimed.
			blocked := errors.New("fixture stops bootstrap before connect")
			f := netns.SchemiiInstallControl(flag, func() error { return blocked })
			defer f.Close()
			monitor := netmon.NewStatic()
			defer monitor.Close()
			// Fail locally before selecting any public bootstrap candidate if a
			// baseline/changed registration bypasses this fixture's stop hook.
			c, err := netns.NewDialer(func(string, ...any) {}, monitor).DialContext(context.Background(), "tcp4", "127.0.0.1:1")
			if c != nil {
				c.Close()
			}
			if !errors.Is(err, blocked) {
				t.Fatalf("unsafe bootstrap fixture: hook bypassed: %v", err)
			}
			beforeFallback := f.Calls()
			var primary atomic.Int32
			forward := &net.Resolver{PreferGo: true, Dial: func(context.Context, string, string) (net.Conn, error) {
				primary.Add(1)
				return nil, errors.New("fixture Android DNS failure")
			}}
			resolver := &dnscache.Resolver{Forward: forward, LookupIPFallback: dnsfallback.MakeLookupFunc(func(string, ...any) {}, monitor)}
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_, _, _, err = resolver.LookupIP(ctx, "schemii-sdk-control.invalid")
			if err == nil || primary.Load() == 0 || f.Calls() <= beforeFallback || f.Owned() == 0 {
				t.Fatalf("real SDK fallback did not reach owned control: err=%v primary=%d binds=%d cookies=%d", err, primary.Load(), f.Calls(), f.Owned())
			}
			before := f.Calls()
			if err := f.Revoke(); err != nil {
				t.Fatal(err)
			}
			_, err = dnsfallback.MakeLookupFunc(func(string, ...any) {}, monitor)(ctx, "after-revoke.invalid")
			if err == nil || f.Calls() != before || f.Owned() != 0 {
				t.Fatal("bootstrap survived revoked epoch")
			}
		})
	}
}

func TestSchemiiStartupLoggerNilMonitorAndSystemDial(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			blocked := errors.New("fixture stops SDK before connect")
			f := netns.SchemiiInstallControl(flag, func() error { return blocked })
			defer f.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			// tsnet starts logging before its monitor exists. The real logger
			// replaces nil with a static monitor before using netns/fallback.
			c, err := logpolicy.MakeDialFunc(nil, func(string, ...any) {})(ctx, "tcp4", "127.0.0.1:1")
			if c != nil {
				c.Close()
			}
			if err == nil || f.Calls() == 0 {
				t.Fatal("startup logger bypassed mandatory callback")
			}
			monitor := netmon.NewStatic()
			defer monitor.Close()
			dialer := tsdial.NewDialer(monitor)
			defer dialer.Close()
			before := f.Calls()
			c, err = dialer.SystemDial(ctx, "tcp4", "127.0.0.1:1")
			if c != nil {
				c.Close()
			}
			if !errors.Is(err, blocked) || f.Calls() != before+1 {
				t.Fatal("control SystemDial bypassed mandatory callback", err)
			}
		})
	}
}
