package main

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/netip"
	"sync"
	"time"

	"tailscale.com/net/netmon"
)

// These values describe framework observations, never guessed kernel metadata.
type networkSnapshot struct {
	Generation uint64   `json:"generation"`
	Handle     uint64   `json:"network_handle"`
	Available  bool     `json:"available"`
	Interface  string   `json:"interface"`
	Index      int      `json:"index"`
	MTU        int      `json:"mtu"`
	Flags      uint     `json:"flags"`
	Addresses  []string `json:"addresses"`
	Gateway    string   `json:"gateway"`
	PrivateDNS bool     `json:"private_dns"`
	Reason     string   `json:"reason"`
}

func (s networkSnapshot) interfaces() ([]netmon.Interface, error) {
	if !s.Available || s.Generation == 0 || s.Index <= 0 || s.MTU <= 0 || s.Interface == "" || len(s.Addresses) == 0 {
		return nil, errors.New("Android network observation unavailable or incomplete")
	}
	addresses := make([]net.Addr, 0, len(s.Addresses))
	for _, text := range s.Addresses {
		prefix, err := netip.ParsePrefix(text)
		if err != nil || prefix.Addr().IsUnspecified() || prefix.Addr().IsLoopback() || prefix.Addr().IsMulticast() {
			return nil, errors.New("Android network supplied an invalid interface address")
		}
		bits := 128
		if prefix.Addr().Is4() {
			bits = 32
		}
		addresses = append(addresses, &net.IPNet{IP: net.IP(prefix.Addr().AsSlice()), Mask: net.CIDRMask(prefix.Bits(), bits)})
	}
	flags := net.Flags(s.Flags)
	if flags&net.FlagUp == 0 || flags&net.FlagLoopback != 0 {
		return nil, errors.New("Android network interface is not an active underlay")
	}
	return []netmon.Interface{{Interface: &net.Interface{Name: s.Interface, Index: s.Index, MTU: s.MTU, Flags: flags}, AltAddrs: addresses}}, nil
}

// Android's DNS policy stays in DnsResolver.rawQuery(Network,...). The Go
// resolver sees an in-memory stream connection, so even its UDP requests use
// DNS-over-stream framing here; no DNS packet/socket is sent by Go itself.
func bridgeResolver(exchange func(context.Context, []byte) ([]byte, error)) *net.Resolver {
	return &net.Resolver{PreferGo: true, StrictErrors: true, Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		if network != "udp" && network != "tcp" {
			return nil, errors.New("unsupported DNS transport")
		}
		ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		client, server := net.Pipe()
		conn := &dnsBridgeConn{Conn: client, cancel: cancel}
		go func() {
			defer server.Close()
			defer cancel()
			stop := context.AfterFunc(ctx, func() { server.Close() })
			defer stop()
			var size [2]byte
			if _, err := io.ReadFull(server, size[:]); err != nil {
				return
			}
			n := int(binary.BigEndian.Uint16(size[:]))
			if n < 12 {
				return
			}
			query := make([]byte, n)
			if _, err := io.ReadFull(server, query); err != nil {
				return
			}
			answer, err := exchange(ctx, query)
			if err != nil || len(answer) < 12 || len(answer) > 65535 || answer[0] != query[0] || answer[1] != query[1] {
				return
			}
			binary.BigEndian.PutUint16(size[:], uint16(len(answer)))
			if _, err := server.Write(size[:]); err != nil {
				return
			}
			_, _ = server.Write(answer)
		}()
		return conn, nil
	}}
}

type dnsBridgeConn struct {
	net.Conn
	cancel context.CancelFunc
	mu     sync.Mutex
	timer  *time.Timer
}

func (c *dnsBridgeConn) Close() error {
	c.mu.Lock()
	if c.timer != nil {
		c.timer.Stop()
	}
	c.mu.Unlock()
	c.cancel()
	return c.Conn.Close()
}

func (c *dnsBridgeConn) SetDeadline(t time.Time) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.timer != nil {
		c.timer.Stop()
	}
	if !t.IsZero() {
		c.timer = time.AfterFunc(time.Until(t), c.cancel)
	}
	return c.Conn.SetDeadline(t)
}
