package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
	"tailscale.com/net/dnscache"
	"tailscale.com/net/netmon"
	"tailscale.com/net/netns"
	"tailscale.com/util/eventbus"
)

func propertyFixture(name string, operation uint) (*unix.Ifreq, error) {
	req, err := unix.NewIfreq(name)
	if err != nil {
		return nil, err
	}
	switch operation {
	case unix.SIOCGIFINDEX:
		req.SetUint32(36)
	case unix.SIOCGIFFLAGS:
		req.SetUint16(unix.IFF_UP | unix.IFF_RUNNING | unix.IFF_BROADCAST | unix.IFF_MULTICAST)
	case unix.SIOCGIFMTU:
		req.SetUint32(1500)
	case unix.SIOCGIFNETMASK:
		err = req.SetInet4Addr([]byte{255, 255, 255, 0})
	default:
		return nil, errors.New("unexpected ioctl")
	}
	return req, err
}

func TestInterfaceRecordsPreserveKernelAddressesAndMetadata(t *testing.T) {
	row := make([]byte, 40)
	copy(row, "wlan0")
	binary.NativeEndian.PutUint16(row[16:18], unix.AF_INET)
	copy(row[20:24], []byte{192, 0, 2, 4})
	records, err := parseInterfaceRecords(row)
	if err != nil {
		t.Fatal(err)
	}
	list, err := interfaceList(records, propertyFixture)
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || list[0].Name != "wlan0" || list[0].Index != 36 || list[0].MTU != 1500 || list[0].Flags != (net.FlagUp|net.FlagRunning|net.FlagBroadcast|net.FlagMulticast) {
		t.Fatal("observed kernel metadata changed")
	}
	if list[0].AltAddrs == nil {
		t.Fatal("SDK could fall back to blocked net.Interface.Addrs")
	}
	addresses, err := list[0].Addrs()
	if err != nil || len(addresses) != 1 || addresses[0].String() != "192.0.2.4/24" {
		t.Fatal("observed address/prefix changed")
	}
	for _, bad := range [][]byte{row[:39], append(append([]byte{}, row...), 1), make([]byte, 40)} {
		if _, err := parseInterfaceRecords(bad); err == nil {
			t.Fatal("invalid ioctl frame accepted")
		}
	}
}

func TestInterfaceObservationNeverFallsBackOrFabricates(t *testing.T) {
	records := []ipv4Record{{name: "wlan0", address: net.IP{192, 0, 2, 4}}}
	if _, err := interfaceList(records, func(string, uint) (*unix.Ifreq, error) { return nil, unix.EPERM }); err == nil {
		t.Fatal("denied ioctl was hidden by fallback")
	}
	if _, err := interfaceList(nil, propertyFixture); err == nil {
		t.Fatal("empty interface list was synthesized")
	}
	if _, err := interfaceList([]ipv4Record{{name: "lo", address: net.IP{127, 0, 0, 1}}}, propertyFixture); err == nil {
		t.Fatal("loopback-only observation was accepted")
	}
	if _, err := interfaceList(append(records, ipv4Record{name: "wlan0", address: net.IP{192, 0, 2, 5}}), propertyFixture); err == nil {
		t.Fatal("ambiguous secondary prefix was invented")
	}
	if _, err := interfaceList(records, func(name string, operation uint) (*unix.Ifreq, error) {
		req, err := propertyFixture(name, operation)
		if operation == unix.SIOCGIFNETMASK {
			req.SetInet4Addr([]byte{255, 0, 255, 0})
		}
		return req, err
	}); err == nil {
		t.Fatal("noncontiguous prefix was accepted")
	}
}

func TestNativeTermuxAndExplicitVPNResolver(t *testing.T) {
	prefix := "/data/data/com.termux/files/usr"
	if err := validateNativeTermux(prefix, 10442, prefix+"/etc/tls/cert.pem"); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		prefix string
		uid    int
		roots  string
	}{{prefix, 1000, prefix + "/etc/tls/cert.pem"}, {"/usr", 10442, "/etc/ssl/cert.pem"}, {prefix, 10442, "/private/schemii/ca.crt"}} {
		if err := validateNativeTermux(test.prefix, test.uid, test.roots); err == nil {
			t.Fatal("guest or private-CA public trust configuration accepted")
		}
	}
	if _, err := vpnResolver("127.0.0.1:53"); err == nil {
		t.Fatal("ambient DNS endpoint accepted")
	}
	if _, err := vpnResolver(androidDNSServer); err != nil {
		t.Fatal(err)
	}
	want := errors.New("unit dial sentinel")
	calls := 0
	resolver := resolverTo(androidDNSServer, func(ctx context.Context, network, address string) (net.Conn, error) {
		calls++
		if address != androidDNSServer || network != "udp" {
			t.Fatal("DNS used ambient address instead of actual VPN resolver")
		}
		return nil, want
	})
	if !resolver.PreferGo {
		t.Fatal("native libc resolver was selected")
	}
	if _, err := resolver.Dial(context.Background(), "udp", "ambient.invalid:53"); err != want || calls != 1 {
		t.Fatal("explicit DNS dial failed")
	}
	if _, err := resolver.Dial(context.Background(), "unix", "ambient.invalid"); err == nil || calls != 1 {
		t.Fatal("unsupported DNS transport was attempted")
	}
}

// The subprocess alone loses NETLINK and proc-file access. No node is started.
// Its real ioctl provider must still feed the pinned SDK's static and polling
// monitor paths, with non-nil alternate addresses and no default-route guess.
func TestNativeNetworkWithoutNetlink(t *testing.T) {
	if os.Getenv("SCHEMII_NETLINK_POISON") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		defer cancel()
		command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestNativeNetworkWithoutNetlink$", "-test.v")
		command.Env = append(os.Environ(), "SCHEMII_NETLINK_POISON=1")
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("poisoned SDK boundary failed: %v %s", err, output)
		}
		if !bytes.Contains(output, []byte("NATIVE_IOCTL_WITHOUT_NETLINK_OK")) {
			t.Fatal("child boundary proof did not run")
		}
		return
	}
	if err := denyNetlinkAndProcFiles(); err != nil {
		t.Fatal(err)
	}
	if _, err := net.Interfaces(); err == nil {
		t.Fatal("poison did not deny Go's netlink enumeration")
	}
	want := errors.New("unit interface provider denied")
	netmon.RegisterInterfaceGetter(func() ([]netmon.Interface, error) { return nil, want })
	if _, err := netmon.GetInterfaceList(); err != want {
		t.Fatal("SDK fell back after an explicit provider error")
	}
	empty := netmon.Interface{Interface: &net.Interface{Index: -1, Name: "not-a-kernel-interface"}, AltAddrs: []net.Addr{}}
	if addresses, err := empty.Addrs(); err != nil || addresses == nil || len(addresses) != 0 {
		t.Fatal("SDK tried netlink for a non-nil empty alternate address list")
	}
	resolver, _ := vpnResolver(androidDNSServer)
	originalResolver := net.DefaultResolver
	installNetworkAdapters(resolver, androidIPv4Interfaces)
	if net.DefaultResolver != originalResolver || !net.DefaultResolver.PreferGo || net.DefaultResolver.Dial == nil || dnscache.Get().Forward != originalResolver {
		t.Fatal("SDK captured a different resolver")
	}
	list, err := netmon.GetInterfaceList()
	if err != nil || len(list) == 0 {
		t.Fatalf("ioctl getter failed under denied netlink: %v", err)
	}
	for _, iface := range list {
		if iface.AltAddrs == nil {
			t.Fatal("nil AltAddrs reenabled netlink")
		}
		if _, err := iface.Addrs(); err != nil {
			t.Fatal(err)
		}
	}
	static := netmon.NewStatic()
	if static.InterfaceState() == nil || !static.InterfaceState().HaveV4 {
		t.Fatal("static logger monitor could not observe real IPv4")
	}
	bus := eventbus.New()
	defer bus.Close()
	var diagnostics strings.Builder
	monitor, err := netmon.New(bus, func(format string, args ...any) { fmt.Fprintf(&diagnostics, format, args...) })
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(diagnostics.String(), "falling back to polling") {
		t.Fatal("SDK did not use supported polling monitor")
	}
	if netns.Listener(func(string, ...any) {}, monitor).Control != nil {
		t.Fatal("privileged Linux socket binding remained enabled")
	}
	monitor.Start()
	if err := monitor.Close(); err != nil {
		t.Fatal(err)
	}
	fmt.Println("NATIVE_IOCTL_WITHOUT_NETLINK_OK")
}

func denyNetlinkAndProcFiles() error {
	filter := []unix.SockFilter{
		{Code: unix.BPF_LD | unix.BPF_W | unix.BPF_ABS, K: 0},
		{Code: unix.BPF_JMP | unix.BPF_JEQ | unix.BPF_K, K: uint32(unix.SYS_OPENAT), Jt: 4},
		{Code: unix.BPF_JMP | unix.BPF_JEQ | unix.BPF_K, K: uint32(unix.SYS_SOCKET), Jf: 2},
		{Code: unix.BPF_LD | unix.BPF_W | unix.BPF_ABS, K: 16},
		{Code: unix.BPF_JMP | unix.BPF_JEQ | unix.BPF_K, K: unix.AF_NETLINK, Jt: 1},
		{Code: unix.BPF_RET | unix.BPF_K, K: unix.SECCOMP_RET_ALLOW},
		{Code: unix.BPF_RET | unix.BPF_K, K: unix.SECCOMP_RET_ERRNO | uint32(unix.EACCES)},
	}
	if err := unix.Prctl(unix.PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0); err != nil {
		return err
	}
	program := unix.SockFprog{Len: uint16(len(filter)), Filter: &filter[0]}
	_, _, errno := unix.Syscall(unix.SYS_SECCOMP, unix.SECCOMP_SET_MODE_FILTER, unix.SECCOMP_FILTER_FLAG_TSYNC, uintptr(unsafe.Pointer(&program)))
	if errno != 0 {
		return errno
	}
	return nil
}
