//go:build darwin

package main

import "golang.org/x/sys/unix"

// enterCbreak disables line buffering and echo but keeps output processing
// (unlike term.MakeRaw), so streamed output renders normally. VMIN=0/VTIME=2
// makes reads return within ~200ms so the listener goroutine can exit.
func enterCbreak(fd int) (func(), error) {
	old, err := unix.IoctlGetTermios(fd, unix.TIOCGETA)
	if err != nil {
		return nil, err
	}
	raw := *old
	raw.Lflag &^= unix.ICANON | unix.ECHO
	raw.Cc[unix.VMIN] = 0
	raw.Cc[unix.VTIME] = 2
	if err := unix.IoctlSetTermios(fd, unix.TIOCSETA, &raw); err != nil {
		return nil, err
	}
	return func() { unix.IoctlSetTermios(fd, unix.TIOCSETA, old) }, nil
}
