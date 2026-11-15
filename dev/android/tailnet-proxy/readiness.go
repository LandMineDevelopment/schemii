package main

import (
	"errors"
	"net/http"

	"tailscale.com/ipn/ipnstate"
)

func platformHandler(next http.Handler, platform networkPlatform) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !platform.ingressAvailable() {
			http.Error(w, "Schemii network is unavailable", http.StatusServiceUnavailable)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *statusStore) pinIdentity(host string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expectedHost = host
}

func (s *statusStore) observeNode(st *ipnstate.Status, generation uint64, available bool) error {
	host, origin, ips := identity(st)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.expectedHost != "" && host != s.expectedHost {
		s.value.Online, s.value.CertificateReady, s.value.Listening = false, false, false
		if err := s.writeLocked(); err != nil {
			return err
		}
		return errors.New("authenticated tailnet identity changed; restart with verified host configuration")
	}
	s.value.BackendState = st.BackendState
	s.value.Online = st.BackendState == "Running"
	s.value.Hostname, s.value.Origin, s.value.TailnetIPs, s.value.LoginURL = host, origin, ips, st.AuthURL
	// Never let a completed old-generation probe overwrite a newer callback.
	if generation >= s.value.NetworkGeneration {
		s.value.NetworkGeneration, s.value.NetworkAvailable = generation, available
		s.value.VerifiedNetworkGeneration = 0
		if available {
			s.value.VerifiedNetworkGeneration = generation
		}
	}
	return s.writeLocked()
}
