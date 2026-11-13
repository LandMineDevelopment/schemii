package main

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestInitializationDeadlineDoesNotWaitForBlockedSDK(t *testing.T) {
	release := make(chan struct{})
	returned := make(chan struct{})
	defer close(release)
	started := time.Now()
	complete, err := initializeNode(context.Background(), func() error { defer close(returned); <-release; return nil }, 20*time.Millisecond)
	if complete || err == nil || time.Since(started) > time.Second {
		t.Fatalf("blocked initialization was not bounded: complete=%v err=%v", complete, err)
	}
	select {
	case <-returned:
		t.Fatal("test SDK unexpectedly completed")
	default:
	}
}

func TestInitializationCancellationAndCompletedError(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	called := false
	complete, err := initializeNode(ctx, func() error { called = true; return nil }, time.Second)
	if complete || called || !errors.Is(err, context.Canceled) {
		t.Fatal("already-cancelled initialization started SDK")
	}
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	release := make(chan struct{})
	started := make(chan struct{})
	defer close(release)
	go func() { <-started; cancel() }()
	complete, err = initializeNode(ctx, func() error { close(started); <-release; return nil }, time.Second)
	if complete || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation waited for blocked SDK")
	}
	want := errors.New("unit startup failure")
	complete, err = initializeNode(context.Background(), func() error { return want }, time.Second)
	if !complete || err != want {
		t.Fatal("finished SDK error lost")
	}
}

func TestSDKDiagnosticsAreSanitizedBoundedAndUseful(t *testing.T) {
	var output bytes.Buffer
	logger := diagnosticLogger(&output)
	logger("netlink initialization failed: permission denied errno=13")
	logger("error fetching https://login.tailscale.com/a/private-login?token=hidden token=private-token tskey-auth-unitsecret privkey:abcdef")
	logger("routine status without a failure")
	for range 500 {
		logger("network observation error: %s", strings.Repeat("x", 2000))
	}
	text := output.String()
	for _, secret := range []string{"private-login", "private-token", "unitsecret", "abcdef", "https://"} {
		if strings.Contains(text, secret) {
			t.Fatalf("diagnostic leaked %s", secret)
		}
	}
	if !strings.Contains(text, "permission denied errno=13") || len(text) > 16*1024 {
		t.Fatal("diagnostics lost useful failure or exceeded budget")
	}
}
