//go:build linux && !android

package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	syscall.Umask(0077)
	cfg := config{}
	flag.StringVar(&cfg.StateDir, "state-dir", "", "private persistent node directory")
	flag.StringVar(&cfg.StatusFile, "status-file", "", "private status JSON outside node directory")
	flag.StringVar(&cfg.Hostname, "hostname", "schemii-phone", "requested dedicated node hostname")
	flag.StringVar(&cfg.Upstream, "upstream", "https://127.0.0.1:8001", "existing local HTTPS ingress")
	flag.StringVar(&cfg.UpstreamCA, "upstream-ca", "", "existing local ingress CA PEM")
	flag.StringVar(&cfg.DNSServer, "dns-server", androidDNSServer, "connected Android Tailscale VPN DNS resolver")
	version := flag.Bool("version", false, "report the pinned tailnet library version without starting")
	flag.Parse()
	if *version {
		fmt.Println("schemii-tailnet-proxy tsnet " + tailscaleVersion)
		return
	}
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "Unexpected positional arguments")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := run(ctx, cfg); err != nil {
		fmt.Fprintln(os.Stderr, "Schemii tailnet: "+err.Error())
		os.Exit(1)
	}
}
