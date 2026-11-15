package netns

import (
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"tailscale.com/net/netmon"
)

func TestSchemiiAndroidControlOwnsAndRevokes(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprintf("disable_optional_%v", flag), func(t *testing.T) {
			f := SchemiiInstallControl(flag, nil)
			defer f.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			monitor := netmon.NewStatic()
			defer monitor.Close()
			listener, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			tcp, err := NewDialer(t.Logf, monitor).DialContext(ctx, "tcp4", listener.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			defer tcp.Close()
			peer, err := listener.Accept()
			if err != nil {
				t.Fatal(err)
			}
			defer peer.Close()
			udp, err := Listener(t.Logf, monitor).ListenPacket(ctx, "udp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer udp.Close()
			if f.Calls() != 2 || f.Owned() != 2 {
				t.Fatalf("SDK bypassed binding/cookie registration: calls=%d owned=%d", f.Calls(), f.Owned())
			}
			if _, err := tcp.Write([]byte("before")); err != nil {
				t.Fatal(err)
			}
			if _, err := udp.WriteTo([]byte("before"), udp.LocalAddr()); err != nil {
				t.Fatal(err)
			}
			if err := f.Revoke(); err != nil {
				t.Fatal(err)
			}
			_ = tcp.SetWriteDeadline(time.Now().Add(time.Second))
			if _, err := tcp.Write([]byte("after")); err == nil {
				t.Fatal("revoked TCP still writes")
			}
			if _, err := udp.WriteTo([]byte("after"), udp.LocalAddr()); err == nil {
				t.Fatal("revoked UDP still writes")
			}
			if c, err := NewDialer(t.Logf, monitor).DialContext(ctx, "tcp4", listener.Addr().String()); err == nil {
				c.Close()
				t.Fatal("SDK dial survived revoked epoch")
			}
			if c, err := Listener(t.Logf, monitor).ListenPacket(ctx, "udp4", "127.0.0.1:0"); err == nil {
				c.Close()
				t.Fatal("SDK listener survived revoked epoch")
			}
			if f.Calls() != 2 || f.Owned() != 0 {
				t.Fatal("revoked epoch bound a new socket")
			}
		})
	}
}

func TestSchemiiAndroidControlPropagatesHookFailure(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			denied := errors.New("fixture rejects before connect")
			f := SchemiiInstallControl(flag, func() error { return denied })
			defer f.Close()
			monitor := netmon.NewStatic()
			defer monitor.Close()
			c, err := NewDialer(t.Logf, monitor).DialContext(context.Background(), "tcp4", "127.0.0.1:1")
			if c != nil {
				c.Close()
			}
			if !errors.Is(err, denied) || f.Calls() != 1 {
				t.Fatalf("lost mandatory hook error: %v, calls=%d", err, f.Calls())
			}
		})
	}
}
