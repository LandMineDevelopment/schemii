package main

import (
	"context"
	"fmt"
	"io"
	"regexp"
	"strings"
	"sync"
	"time"
)

// The SDK forbids Close concurrently with Start. If a kernel observation blocks
// initialization, return to main, which exits this helper process; do not wait
// indefinitely or try to close a half-initialized backend from another goroutine.
func initializeNode(ctx context.Context, start func() error, limit time.Duration) (finished bool, err error) {
	if err := ctx.Err(); err != nil {
		return false, fmt.Errorf("tailnet SDK initialization interrupted: %w", err)
	}
	result := make(chan error, 1)
	go func() { result <- start() }()
	timer := time.NewTimer(limit)
	defer timer.Stop()
	select {
	case err := <-result:
		return true, err
	case <-ctx.Done():
		return false, fmt.Errorf("tailnet SDK initialization interrupted: %w", ctx.Err())
	case <-timer.C:
		return false, fmt.Errorf("tailnet SDK initialization exceeded %s while observing the Android network", limit)
	}
}

var diagnosticURL = regexp.MustCompile(`https?://[^\s<>"']+`)
var diagnosticKey = regexp.MustCompile(`(?i)(tskey-[a-z0-9_-]+|(?:privkey|machinekey|nodekey):[a-f0-9]+)`)
var diagnosticSecret = regexp.MustCompile(`(?i)(authorization|authkey|token|secret|password)\s*[:=]\s*[^\s,;]+`)

func diagnosticLogger(output io.Writer) func(string, ...any) {
	var lock sync.Mutex
	remaining := 16 * 1024
	return func(format string, args ...any) {
		message := fmt.Sprintf(format, args...)
		lower := strings.ToLower(message)
		interesting := false
		for _, marker := range []string{"error", "failed", "failure", "denied", "unsupported", "netlink", "initialization", "network observation"} {
			interesting = interesting || strings.Contains(lower, marker)
		}
		if !interesting {
			return
		}
		if strings.Contains(lower, "private key-----") {
			message = "sensitive SDK diagnostic omitted"
		}
		message = diagnosticURL.ReplaceAllString(message, "[URL]")
		message = diagnosticKey.ReplaceAllString(message, "[key]")
		message = diagnosticSecret.ReplaceAllString(message, "$1=[redacted]")
		message = strings.Join(strings.Fields(strings.ReplaceAll(message, "\x00", "")), " ")
		if len(message) > 1024 {
			message = strings.ToValidUTF8(message[:1024], "?")
		}
		line := "Tailscale SDK: " + message + "\n"
		lock.Lock()
		defer lock.Unlock()
		if len(line) > remaining {
			return
		}
		_, _ = io.WriteString(output, line)
		remaining -= len(line)
	}
}
