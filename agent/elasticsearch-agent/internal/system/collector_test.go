package system

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

type procFixture struct {
	root string
}

func (p procFixture) write(t *testing.T, name, content string) {
	t.Helper()
	path := filepath.Join(p.root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func fakeStatFS(string) (uint64, uint64, uint64, error) {
	// 100 GiB disk, 60 GiB free, 55 GiB available to non-root.
	return 100 << 30, 60 << 30, 55 << 30, nil
}

func TestCollect(t *testing.T) {
	proc := procFixture{root: t.TempDir()}
	proc.write(t, "meminfo", "MemTotal:       16384000 kB\nMemFree:  1000 kB\nMemAvailable:    4096000 kB\n")
	proc.write(t, "loadavg", "1.50 1.20 1.00 2/300 12345\n")
	proc.write(t, "uptime", "3600.25 7000.00\n")
	proc.write(t, "mounts", "/dev/sdb /var/lib/elasticsearch ext4 rw 0 0\n/dev/sda1 / ext4 rw 0 0\n")
	proc.write(t, "stat", "cpu  1000 0 1000 8000 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
	proc.write(t, "diskstats", "   8      16 sdb 100 0 2048 0 50 0 4096 0 0 0 0\n")
	proc.write(t, "net/dev", "Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes\n    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n  ens4: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n")

	collector := NewCollector(proc.root, "/var/lib/elasticsearch", fakeStatFS)
	start := time.Unix(1_700_000_000, 0)
	first := collector.Collect(start)
	if first.CPUPercent != nil || first.NetworkRxBytesPerSec != nil {
		t.Fatal("rates need two samples")
	}
	if *first.MemoryPercent != 75 {
		t.Fatalf("memory percent = %v", *first.MemoryPercent)
	}
	if *first.DiskPercent != 42.1 { // 40 GiB used / (40 GiB used + 55 GiB available), like df
		t.Fatalf("disk percent = %v", *first.DiskPercent)
	}
	if *first.Load1 != 1.5 || *first.UptimeSeconds != 3600.25 {
		t.Fatalf("load/uptime = %v/%v", *first.Load1, *first.UptimeSeconds)
	}

	proc.write(t, "stat", "cpu  1500 0 1500 9000 0 0 0 0 0 0\n")
	proc.write(t, "diskstats", "   8      16 sdb 100 0 4096 0 50 0 14336 0 0 0 0\n")
	proc.write(t, "net/dev", "x|y\nx|y\n    lo: 5000 0 0 0 0 0 0 0 5000 0 0 0 0 0 0 0\n  ens4: 11000 0 0 0 0 0 0 0 7000 0 0 0 0 0 0 0\n")
	second := collector.Collect(start.Add(10 * time.Second))
	if second.CPUPercent == nil || *second.CPUPercent != 50 {
		t.Fatalf("cpu percent = %v", second.CPUPercent)
	}
	if *second.DiskReadBytesPerSec != 104857.6 || *second.DiskWriteBytesPerSec != 524288 {
		t.Fatalf("disk rates = %v/%v", *second.DiskReadBytesPerSec, *second.DiskWriteBytesPerSec)
	}
	if *second.NetworkRxBytesPerSec != 1000 || *second.NetworkTxBytesPerSec != 500 {
		t.Fatalf("network rates exclude loopback: %v/%v", *second.NetworkRxBytesPerSec, *second.NetworkTxBytesPerSec)
	}
}

func TestMissingProcIsNotFatal(t *testing.T) {
	collector := NewCollector(t.TempDir(), "/nowhere", func(string) (uint64, uint64, uint64, error) {
		return 0, 0, 0, os.ErrNotExist
	})
	metrics := collector.Collect(time.Now())
	if metrics.MemoryPercent != nil || metrics.DiskPercent != nil {
		t.Fatal("unavailable metrics must be omitted, not zero")
	}
}
