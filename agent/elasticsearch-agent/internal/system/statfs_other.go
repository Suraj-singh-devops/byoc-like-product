//go:build !linux && !darwin

package system

import "errors"

func statFS(string) (uint64, uint64, uint64, error) {
	return 0, 0, 0, errors.New("statfs is not supported on this platform")
}
