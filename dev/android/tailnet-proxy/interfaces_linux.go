package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"runtime"
	"sort"
	"unsafe"

	"golang.org/x/sys/unix"
	"tailscale.com/net/netmon"
)

// Android app UIDs cannot use Go's net.Interface.Addrs (RTM_GETADDR).
// SIOCGIFCONF is permitted and reports real configured IPv4 addresses. This
// intentionally supports IPv4 observation only; it never invents an interface,
// IPv6 address, prefix or route when Android denies the richer kernel APIs.
const interfaceObservationMode = "android-ipv4-ioctl"

type ipv4Record struct {
	name    string
	address net.IP
}
type interfaceProperty func(string, uint) (*unix.Ifreq, error)

func parseInterfaceRecords(data []byte) ([]ipv4Record, error) {
	const stride = 40 // Linux 64-bit struct ifreq: IFNAMSIZ16 + union24.
	if len(data)%stride != 0 {
		return nil, errors.New("Android interface response has an invalid length")
	}
	var records []ipv4Record
	for offset := 0; offset < len(data); offset += stride {
		row := data[offset : offset+stride]
		end := bytes.IndexByte(row[:unix.IFNAMSIZ], 0)
		if end <= 0 || binary.NativeEndian.Uint16(row[16:18]) != unix.AF_INET {
			return nil, errors.New("Android interface response has an invalid name or address family")
		}
		records = append(records, ipv4Record{name: string(row[:end]), address: append(net.IP(nil), row[20:24]...)})
	}
	return records, nil
}

func interfaceList(records []ipv4Record, property interfaceProperty) ([]netmon.Interface, error) {
	var result []netmon.Interface
	seen := map[string]net.IP{}
	active := false
	for _, record := range records {
		if previous, exists := seen[record.name]; exists {
			if previous.Equal(record.address) {
				continue
			}
			return nil, errors.New("Android interface has multiple IPv4 addresses under one label; cannot verify each prefix")
		}
		seen[record.name] = record.address
		index, err := property(record.name, unix.SIOCGIFINDEX)
		if err != nil {
			return nil, fmt.Errorf("Android interface index ioctl failed: %w", err)
		}
		flags, err := property(record.name, unix.SIOCGIFFLAGS)
		if err != nil {
			return nil, fmt.Errorf("Android interface flags ioctl failed: %w", err)
		}
		mtu, err := property(record.name, unix.SIOCGIFMTU)
		if err != nil {
			return nil, fmt.Errorf("Android interface MTU ioctl failed: %w", err)
		}
		mask, err := property(record.name, unix.SIOCGIFNETMASK)
		if err != nil {
			return nil, fmt.Errorf("Android interface netmask ioctl failed: %w", err)
		}
		maskBytes, err := mask.Inet4Addr()
		if err != nil {
			return nil, errors.New("Android interface netmask is not IPv4")
		}
		prefixMask := net.IPMask(append([]byte(nil), maskBytes...))
		_, bits := prefixMask.Size()
		if bits != 32 || index.Uint32() == 0 || index.Uint32() > 1<<31-1 || mtu.Uint32() == 0 || mtu.Uint32() > 1<<31-1 {
			return nil, errors.New("Android interface metadata is invalid")
		}
		var observed net.Flags
		for _, flag := range []struct {
			kernel uint16
			goFlag net.Flags
		}{
			{unix.IFF_UP, net.FlagUp}, {unix.IFF_BROADCAST, net.FlagBroadcast}, {unix.IFF_LOOPBACK, net.FlagLoopback},
			{unix.IFF_POINTOPOINT, net.FlagPointToPoint}, {unix.IFF_RUNNING, net.FlagRunning}, {unix.IFF_MULTICAST, net.FlagMulticast},
		} {
			if flags.Uint16()&flag.kernel != 0 {
				observed |= flag.goFlag
			}
		}
		if observed&net.FlagUp != 0 && !record.address.IsLoopback() && !record.address.IsUnspecified() {
			active = true
		}
		result = append(result, netmon.Interface{
			Interface: &net.Interface{Name: record.name, Index: int(index.Uint32()), MTU: int(mtu.Uint32()), Flags: observed},
			// A non-nil AltAddrs is essential: the SDK must never fall back to
			// net.Interface.Addrs, including when an interface has no IPv6 data.
			AltAddrs: []net.Addr{&net.IPNet{IP: append(net.IP(nil), record.address...), Mask: prefixMask}},
		})
	}
	if !active {
		return nil, errors.New("Android ioctl observation requires an active non-loopback IPv4 interface; IPv6-only observation is unsupported")
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Index < result[j].Index })
	return result, nil
}

func androidIPv4Interfaces() ([]netmon.Interface, error) {
	if unsafe.Sizeof(uintptr(0)) != 8 {
		return nil, errors.New("Android ioctl observation requires a 64-bit Linux build")
	}
	fd, err := unix.Socket(unix.AF_INET, unix.SOCK_DGRAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("Android interface control socket failed: %w", err)
	}
	defer unix.Close(fd)
	buffer := make([]byte, 64*1024)
	request := struct {
		length int32
		buffer unsafe.Pointer
	}{length: int32(len(buffer)), buffer: unsafe.Pointer(&buffer[0])}
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), unix.SIOCGIFCONF, uintptr(unsafe.Pointer(&request)))
	runtime.KeepAlive(buffer)
	if errno != 0 {
		return nil, fmt.Errorf("Android interface enumeration ioctl failed: %w", errno)
	}
	if request.length < 0 || int(request.length) >= len(buffer)-40 {
		return nil, errors.New("Android interface response was truncated")
	}
	records, err := parseInterfaceRecords(buffer[:int(request.length)])
	if err != nil {
		return nil, err
	}
	return interfaceList(records, func(name string, operation uint) (*unix.Ifreq, error) {
		req, err := unix.NewIfreq(name)
		if err != nil {
			return nil, err
		}
		if err := unix.IoctlIfreq(fd, operation, req); err != nil {
			return nil, err
		}
		return req, nil
	})
}
