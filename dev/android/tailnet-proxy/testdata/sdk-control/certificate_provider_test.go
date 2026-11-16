package main

import (
	"context"
	"fmt"
	"os"
	"testing"

	"tailscale.com/net/netns"
)

func TestSchemiiACMEUsesMandatoryAndroidSockets(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			ownership := netns.SchemiiInstallControl(flag, nil)
			defer ownership.Close()
			f := newACMEFixture(t)
			p := fixtureProvider(t, f, context.Background(), ownership.Current)
			if _, err := fetchPublicCertificate(context.Background(), p, p.directory, p.host, p.roots); err != nil {
				t.Fatal(err)
			}
			if ownership.Calls() == 0 || ownership.Owned() == 0 {
				t.Fatal("actual ACME transport bypassed Android binding/cookie ownership")
			}
			if err := ownership.Revoke(); err != nil {
				t.Fatal(err)
			}
			before := ownership.Calls()
			if _, err := fetchPublicCertificate(context.Background(), p, p.directory, p.host, p.roots); err == nil {
				t.Fatal("cached certificate survived revoked epoch")
			}
			if ownership.Calls() != before {
				t.Fatal("revoked provider created a socket")
			}
		})
	}
}

func TestSchemiiACMEActiveEpochRevocationCannotCommit(t *testing.T) {
	for _, flag := range []bool{false, true} {
		t.Run(fmt.Sprint(flag), func(t *testing.T) {
			ownership := netns.SchemiiInstallControl(flag, nil)
			defer ownership.Close()
			f := newACMEFixture(t)
			f.configure(func() {
				f.onCertificate = func() {
					if err := ownership.Revoke(); err != nil {
						t.Error(err)
					}
				}
			})
			p := fixtureProvider(t, f, context.Background(), ownership.Current)
			if _, _, err := p.CertPair(context.Background(), p.host); err == nil {
				t.Fatal("active revoked ACME operation succeeded")
			}
			if _, err := os.Stat(p.path(".crt")); !os.IsNotExist(err) {
				t.Fatal("revoked operation committed certificate", err)
			}
			if ownership.Owned() != 0 {
				t.Fatal("epoch retained live socket ownership")
			}
		})
	}
}
