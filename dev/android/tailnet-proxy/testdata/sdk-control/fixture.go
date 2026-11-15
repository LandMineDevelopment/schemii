// Host control fixture, overlaid into the pinned SDK's netns package only.
package netns

import "sync/atomic"

// The Android JNI call alone is substituted. The SDK control body, adapter hook
// registration statements and socketEpoch implementation come from real source.
type schemiiPlatform struct {
	epoch     *socketEpoch
	calls     atomic.Int32
	afterBind func() error
}

func (p *schemiiPlatform) bindSocket(fd int) error {
	err := p.epoch.bind(fd, func(g uint64, _ int) error {
		if g != 73 {
			panic("fixture generation mismatch")
		}
		p.calls.Add(1)
		return nil
	})
	if err == nil && p.afterBind != nil {
		return p.afterBind()
	}
	return err
}

// SchemiiControlFixture configures exactly the registration statements extracted
// from apkPlatform.install. It is not part of the real SDK or product build.
type SchemiiControlFixture struct{ p *schemiiPlatform }

func SchemiiInstallControl(disableOptional bool, afterBind func() error) *SchemiiControlFixture {
	p := &schemiiPlatform{epoch: newSocketEpoch(73), afterBind: afterBind}
	// The exported SDK setter is Android-only. Host controls assign the same
	// atomic flag directly; they do not rewrite or emulate controlC's predicate.
	disableAndroidBindToActiveNetwork.Store(disableOptional)
	schemiiInstallSource(p)
	return &SchemiiControlFixture{p}
}

func (f *SchemiiControlFixture) Calls() int { return int(f.p.calls.Load()) }
func (f *SchemiiControlFixture) Owned() int {
	f.p.epoch.mu.Lock()
	defer f.p.epoch.mu.Unlock()
	return len(f.p.epoch.sockets)
}
func (f *SchemiiControlFixture) Revoke() error { return f.p.epoch.revoke() }
func (f *SchemiiControlFixture) Close() {
	_ = f.Revoke()
	SetAndroidProtectFunc(nil)
	SetAndroidBindToNetworkFunc(nil)
	disableAndroidBindToActiveNetwork.Store(false)
}
