package main

import (
	"errors"
	"sync"

	"golang.org/x/sys/unix"
)

// The registry holds identities, never duplicate descriptors. A duplicate is
// acquired only during revocation, then its kernel socket cookie is compared
// before shutdown. This cannot affect an unrelated socket that reused an FD.
// A changed epoch is permanently closed; SDK DNS caches belong to that epoch.
type socketEpoch struct {
	mu         sync.Mutex
	generation uint64
	closed     bool
	failure    error
	sockets    map[int]uint64
}

func newSocketEpoch(generation uint64) *socketEpoch {
	return &socketEpoch{generation: generation, sockets: make(map[int]uint64)}
}
func (e *socketEpoch) bind(fd int, bind func(uint64, int) error) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return errors.New("Android socket epoch revoked")
	}
	cookie, err := unix.GetsockoptUint64(fd, unix.SOL_SOCKET, unix.SO_COOKIE)
	if err != nil {
		return errors.New("cannot verify Android kernel socket identity")
	}
	if err := bind(e.generation, fd); err != nil {
		return err
	}
	e.sockets[fd] = cookie
	return nil
}
func (e *socketEpoch) revoke() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return e.failure
	}
	e.closed = true
	for fd, cookie := range e.sockets {
		duplicate, err := unix.FcntlInt(uintptr(fd), unix.F_DUPFD_CLOEXEC, 0)
		if err != nil {
			if !errors.Is(err, unix.EBADF) {
				e.failure = errors.New("cannot duplicate owned socket for safe revocation")
			}
			continue
		}
		observed, err := unix.GetsockoptUint64(duplicate, unix.SOL_SOCKET, unix.SO_COOKIE)
		if err != nil && !errors.Is(err, unix.ENOTSOCK) {
			e.failure = errors.New("cannot verify socket identity during revocation")
		}
		if err == nil && observed == cookie {
			if err := unix.Shutdown(duplicate, unix.SHUT_RDWR); err != nil && !errors.Is(err, unix.ENOTCONN) {
				e.failure = errors.New("owned socket shutdown could not be confirmed")
			}
		}
		_ = unix.Close(duplicate)
	}
	e.sockets = nil
	return e.failure
}
