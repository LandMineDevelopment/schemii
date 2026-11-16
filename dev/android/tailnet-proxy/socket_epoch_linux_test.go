package main

import (
	"net"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func TestSocketEpochRevokesRealConnectionAndRejectsLaterBinding(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	epoch := newSocketEpoch(7)
	calls := 0
	dialer := net.Dialer{Control: func(_, _ string, raw syscall.RawConn) error {
		var bindErr error
		if err := raw.Control(func(fd uintptr) {
			bindErr = epoch.bind(int(fd), func(g uint64, fd int) error {
				calls++
				if g != 7 {
					t.Error("wrong epoch")
				}
				return nil
			})
		}); err != nil {
			return err
		}
		return bindErr
	}}
	client, err := dialer.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	server, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	epoch.revoke()
	if calls != 1 {
		t.Fatal("binding hook not called before connect")
	}
	_ = client.SetWriteDeadline(time.Now().Add(time.Second))
	if _, err := client.Write([]byte("must not cross revoked epoch")); err == nil {
		t.Fatal("revoked connection still writes")
	}
	if err := epoch.bind(0, func(uint64, int) error { t.Fatal("revoked epoch called platform binder"); return nil }); err == nil {
		t.Fatal("new bind survived revocation")
	}
	epoch.revoke() // Safe/idempotent, no descriptor ownership transfer.
}

func TestSocketEpochCannotRevokeSocketWithDifferentKernelIdentity(t *testing.T) {
	pair, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer unix.Close(pair[0])
	defer unix.Close(pair[1])
	cookie, err := unix.GetsockoptUint64(pair[0], unix.SOL_SOCKET, unix.SO_COOKIE)
	if err != nil {
		t.Fatal(err)
	}
	epoch := newSocketEpoch(1)
	// This is the state after an FD number has been reused for a new socket.
	epoch.sockets[pair[0]] = cookie + 1
	epoch.revoke()
	if _, err := unix.Write(pair[0], []byte("peer")); err != nil {
		t.Fatal("different socket was shutdown", err)
	}
	bytes := make([]byte, 4)
	if n, err := unix.Read(pair[1], bytes); err != nil || n != 4 || string(bytes) != "peer" {
		t.Fatal("peer data changed")
	}
}

func TestSocketEpochRevokesUnconnectedUDPSend(t *testing.T) {
	fd, err := unix.Socket(unix.AF_INET, unix.SOCK_DGRAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer unix.Close(fd)
	epoch := newSocketEpoch(3)
	if err := epoch.bind(fd, func(uint64, int) error { return nil }); err != nil {
		t.Fatal(err)
	}
	epoch.revoke()
	if err := unix.Sendto(fd, []byte("revoked"), 0, &unix.SockaddrInet4{Port: 9, Addr: [4]byte{127, 0, 0, 1}}); err == nil {
		t.Fatal("unconnected UDP socket can send after epoch revocation")
	}
}
