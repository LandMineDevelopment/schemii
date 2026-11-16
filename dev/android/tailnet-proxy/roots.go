package main

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

// This contains public trust anchors, never the backend CA or node identity.
// Atomic replacement prevents a partial bundle from being consumed by the SDK.
func writePublicRoots(path string, roots []byte) error {
	return writePrivateFile(path, roots)
}

// The destination is a single atomic, fsynced, mode0600 file in a private
// directory. Certificate renewal retains its key, so replacing only the verified
// certificate cannot expose a mixed-key generation.
func writePrivateFile(path string, data []byte) error {
	if err := privateDir(filepath.Dir(path)); err != nil {
		return err
	}
	if info, err := os.Lstat(path); err == nil {
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !info.Mode().IsRegular() || !ok || stat.Nlink != 1 {
			return errors.New("invalid private file destination")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".private-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err = file.Chmod(0600); err == nil {
		_, err = file.Write(data)
	}
	if err == nil {
		err = file.Sync()
	}
	if err = errors.Join(err, file.Close()); err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	parent, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}
