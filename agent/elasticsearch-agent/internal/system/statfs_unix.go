//go:build linux || darwin

package system

import "syscall"

func statFS(path string) (total, free, avail uint64, err error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return 0, 0, 0, err
	}
	size := uint64(st.Bsize)
	return st.Blocks * size, st.Bfree * size, st.Bavail * size, nil
}
