// Package system collects host metrics from /proc and statfs(2).
package system

import (
	"bufio"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Metrics mirrors the "system" section of the node report. Nil means "not available".
type Metrics struct {
	CPUPercent           *float64 `json:"cpu_percent,omitempty"`
	MemoryPercent        *float64 `json:"memory_percent,omitempty"`
	MemoryTotalBytes     *uint64  `json:"memory_total_bytes,omitempty"`
	MemoryUsedBytes      *uint64  `json:"memory_used_bytes,omitempty"`
	DiskPercent          *float64 `json:"disk_percent,omitempty"`
	DiskTotalBytes       *uint64  `json:"disk_total_bytes,omitempty"`
	DiskUsedBytes        *uint64  `json:"disk_used_bytes,omitempty"`
	DiskReadBytesPerSec  *float64 `json:"disk_read_bytes_per_sec,omitempty"`
	DiskWriteBytesPerSec *float64 `json:"disk_write_bytes_per_sec,omitempty"`
	NetworkRxBytesPerSec *float64 `json:"network_rx_bytes_per_sec,omitempty"`
	NetworkTxBytesPerSec *float64 `json:"network_tx_bytes_per_sec,omitempty"`
	Load1                *float64 `json:"load1,omitempty"`
	UptimeSeconds        *float64 `json:"uptime_seconds,omitempty"`
}

type counters struct {
	at        time.Time
	cpuTotal  uint64
	cpuIdle   uint64
	diskRead  uint64
	diskWrite uint64
	netRx     uint64
	netTx     uint64
	haveCPU   bool
	haveDisk  bool
	haveNet   bool
}

// StatFS returns total, free and available bytes of the filesystem containing path.
type StatFS func(path string) (total, free, avail uint64, err error)

// Collector keeps the previous counters to turn totals into rates.
type Collector struct {
	procRoot string
	dataPath string
	statfs   StatFS
	prev     *counters
}

// NewCollector reads /proc under procRoot and disk usage of dataPath.
func NewCollector(procRoot, dataPath string, statfs StatFS) *Collector {
	if statfs == nil {
		statfs = statFS
	}
	return &Collector{procRoot: procRoot, dataPath: dataPath, statfs: statfs}
}

func f64(v float64) *float64 { return &v }
func u64(v uint64) *uint64   { return &v }

func round1(v float64) float64 {
	return float64(int64(v*10+0.5)) / 10
}

// Collect returns a snapshot; rates need two calls.
func (c *Collector) Collect(now time.Time) Metrics {
	var m Metrics
	cur := &counters{at: now}

	if total, idle, ok := c.readCPU(); ok {
		cur.cpuTotal, cur.cpuIdle, cur.haveCPU = total, idle, true
	}
	if total, available, ok := c.readMemory(); ok && total > 0 {
		used := total - available
		m.MemoryTotalBytes = u64(total)
		m.MemoryUsedBytes = u64(used)
		m.MemoryPercent = f64(round1(100 * float64(used) / float64(total)))
	}
	if total, free, avail, err := c.statfs(c.dataPath); err == nil && total > 0 {
		used := total - free
		m.DiskTotalBytes = u64(total)
		m.DiskUsedBytes = u64(used)
		if used+avail > 0 {
			m.DiskPercent = f64(round1(100 * float64(used) / float64(used+avail)))
		}
	}
	if read, write, ok := c.readDiskIO(); ok {
		cur.diskRead, cur.diskWrite, cur.haveDisk = read, write, true
	}
	if rx, tx, ok := c.readNetwork(); ok {
		cur.netRx, cur.netTx, cur.haveNet = rx, tx, true
	}
	if load, ok := c.readFirstFloat("loadavg"); ok {
		m.Load1 = f64(load)
	}
	if uptime, ok := c.readFirstFloat("uptime"); ok {
		m.UptimeSeconds = f64(uptime)
	}

	if p := c.prev; p != nil {
		elapsed := now.Sub(p.at).Seconds()
		if cur.haveCPU && p.haveCPU && cur.cpuTotal > p.cpuTotal {
			busy := float64((cur.cpuTotal-p.cpuTotal)-(cur.cpuIdle-p.cpuIdle)) / float64(cur.cpuTotal-p.cpuTotal)
			m.CPUPercent = f64(round1(100 * busy))
		}
		if elapsed > 0 {
			if cur.haveDisk && p.haveDisk && cur.diskRead >= p.diskRead && cur.diskWrite >= p.diskWrite {
				m.DiskReadBytesPerSec = f64(round1(float64(cur.diskRead-p.diskRead) / elapsed))
				m.DiskWriteBytesPerSec = f64(round1(float64(cur.diskWrite-p.diskWrite) / elapsed))
			}
			if cur.haveNet && p.haveNet && cur.netRx >= p.netRx && cur.netTx >= p.netTx {
				m.NetworkRxBytesPerSec = f64(round1(float64(cur.netRx-p.netRx) / elapsed))
				m.NetworkTxBytesPerSec = f64(round1(float64(cur.netTx-p.netTx) / elapsed))
			}
		}
	}
	c.prev = cur
	return m
}

func (c *Collector) path(name string) string {
	return filepath.Join(c.procRoot, name)
}

func (c *Collector) readCPU() (total, idle uint64, ok bool) {
	file, err := os.Open(c.path("stat"))
	if err != nil {
		return 0, 0, false
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 5 || fields[0] != "cpu" {
			continue
		}
		// user nice system idle iowait irq softirq steal (guest time is already in user).
		for i, field := range fields[1:] {
			if i >= 8 {
				break
			}
			value, err := strconv.ParseUint(field, 10, 64)
			if err != nil {
				return 0, 0, false
			}
			total += value
			if i == 3 || i == 4 {
				idle += value
			}
		}
		return total, idle, true
	}
	return 0, 0, false
}

func (c *Collector) readMemory() (total, available uint64, ok bool) {
	file, err := os.Open(c.path("meminfo"))
	if err != nil {
		return 0, 0, false
	}
	defer file.Close()
	values := map[string]uint64{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 2 {
			continue
		}
		if value, err := strconv.ParseUint(fields[1], 10, 64); err == nil {
			values[strings.TrimSuffix(fields[0], ":")] = value * 1024
		}
	}
	total, okTotal := values["MemTotal"]
	available, okAvail := values["MemAvailable"]
	return total, available, okTotal && okAvail
}

// dataDevice finds the block device mounted at dataPath (e.g. "sdb").
func (c *Collector) dataDevice() string {
	file, err := os.Open(c.path("mounts"))
	if err != nil {
		return ""
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 2 || fields[1] != c.dataPath || !strings.HasPrefix(fields[0], "/dev/") {
			continue
		}
		device := fields[0]
		if resolved, err := filepath.EvalSymlinks(device); err == nil {
			device = resolved
		}
		return filepath.Base(device)
	}
	return ""
}

func (c *Collector) readDiskIO() (readBytes, writeBytes uint64, ok bool) {
	device := c.dataDevice()
	if device == "" {
		return 0, 0, false
	}
	file, err := os.Open(c.path("diskstats"))
	if err != nil {
		return 0, 0, false
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 10 || fields[2] != device {
			continue
		}
		sectorsRead, err1 := strconv.ParseUint(fields[5], 10, 64)
		sectorsWritten, err2 := strconv.ParseUint(fields[9], 10, 64)
		if err1 != nil || err2 != nil {
			return 0, 0, false
		}
		return sectorsRead * 512, sectorsWritten * 512, true
	}
	return 0, 0, false
}

func (c *Collector) readNetwork() (rx, tx uint64, ok bool) {
	file, err := os.Open(c.path("net/dev"))
	if err != nil {
		return 0, 0, false
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		name, rest, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		name = strings.TrimSpace(name)
		fields := strings.Fields(rest)
		if name == "lo" || len(fields) < 9 {
			continue
		}
		received, err1 := strconv.ParseUint(fields[0], 10, 64)
		sent, err2 := strconv.ParseUint(fields[8], 10, 64)
		if err1 != nil || err2 != nil {
			continue
		}
		rx += received
		tx += sent
		ok = true
	}
	return rx, tx, ok
}

func (c *Collector) readFirstFloat(name string) (float64, bool) {
	data, err := os.ReadFile(c.path(name))
	if err != nil {
		return 0, false
	}
	fields := strings.Fields(string(data))
	if len(fields) == 0 {
		return 0, false
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	return value, err == nil
}
