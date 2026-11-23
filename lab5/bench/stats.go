package main

import (
	"math"
	"slices"
)

const chunkSize = 1 << 20

// col — стовпчик значень uint32 (нс), що росте шматками, щоб append не копіював мільйони елементів
// усередині вимірювання. Значення, більші за 4,29 с, насичуються (точний максимум рахується окремо).
type col struct {
	chunks [][]uint32
	cur    []uint32
}

func (c *col) add(v int64) {
	if len(c.cur) == cap(c.cur) {
		if c.cur != nil {
			c.chunks = append(c.chunks, c.cur)
		}
		c.cur = make([]uint32, 0, chunkSize)
	}
	if v < 0 {
		v = 0
	}
	if v > math.MaxUint32 {
		v = math.MaxUint32
	}
	c.cur = append(c.cur, uint32(v))
}

// rec — записи однієї ролі в одному потоці.
type rec struct {
	tau, wait       col
	n               uint64
	sumWait, sumTau int64
	maxWait, maxTau int64
}

func (x *rec) add(tau, wait int64) {
	x.tau.add(tau)
	x.wait.add(wait)
	x.n++
	x.sumTau += tau
	x.sumWait += wait
	if wait > x.maxWait {
		x.maxWait = wait
	}
	if tau > x.maxTau {
		x.maxTau = tau
	}
}

func gather(recs []*rec, pick func(*rec) *col) []uint32 {
	var n int
	for _, r := range recs {
		c := pick(r)
		for _, ch := range c.chunks {
			n += len(ch)
		}
		n += len(c.cur)
	}
	all := make([]uint32, 0, n)
	for _, r := range recs {
		c := pick(r)
		for _, ch := range c.chunks {
			all = append(all, ch...)
		}
		all = append(all, c.cur...)
	}
	return all
}

// quantile: p_q = x_(⌈q·N⌉) на відсортованому за зростанням списку (нумерація з 1).
func quantile(sorted []uint32, q float64) uint32 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	k := int(math.Ceil(q * float64(n)))
	if k < 1 {
		k = 1
	}
	if k > n {
		k = n
	}
	return sorted[k-1]
}

const histBuckets = 100 // 10 корзин на декаду: корзина b містить [10^(b/10), 10^((b+1)/10)) нс

func histogram(sorted []uint32) []int {
	h := make([]int, histBuckets)
	for _, v := range sorted {
		b := 0
		if v > 1 {
			b = int(math.Floor(10 * math.Log10(float64(v))))
		}
		if b >= histBuckets {
			b = histBuckets - 1
		}
		h[b]++
	}
	return h
}

type RoleStats struct {
	Role       string  `json:"role"`
	Threads    int     `json:"threads"`
	N          uint64  `json:"n_ops"`
	X          float64 `json:"x_ops_per_s"`
	XPerThread float64 `json:"x_per_thread"`
	P50Ns      uint32  `json:"p50_ns"`
	P90Ns      uint32  `json:"p90_ns"`
	P99Ns      uint32  `json:"p99_ns"`
	P999Ns     uint32  `json:"p999_ns"`
	MaxTauNs   int64   `json:"max_tau_ns"`
	MeanTauNs  float64 `json:"mean_tau_ns"`
	SumWaitNs  int64   `json:"sum_wait_ns"`
	MaxWaitNs  int64   `json:"max_wait_ns"`
	MeanWaitNs float64 `json:"mean_wait_ns"`
	WaitP50Ns  uint32  `json:"wait_p50_ns"`
	WaitP99Ns  uint32  `json:"wait_p99_ns"`
	W          float64 `json:"w"`
	Cv         float64 `json:"cv"`
	HistTau    []int   `json:"hist_tau"`
	HistWait   []int   `json:"hist_wait"`
}

// roleStats рахує метрики ролі за записами її потоків; d — тривалість вимірювання, с.
func roleStats(name string, recs []*rec, d float64) RoleStats {
	rs := RoleStats{Role: name, Threads: len(recs)}
	var sumTau int64
	counts := make([]float64, len(recs))
	for i, r := range recs {
		rs.N += r.n
		rs.SumWaitNs += r.sumWait
		sumTau += r.sumTau
		if r.maxWait > rs.MaxWaitNs {
			rs.MaxWaitNs = r.maxWait
		}
		if r.maxTau > rs.MaxTauNs {
			rs.MaxTauNs = r.maxTau
		}
		counts[i] = float64(r.n)
	}
	if len(recs) == 0 || rs.N == 0 {
		return rs
	}
	k := float64(len(recs))
	rs.X = float64(rs.N) / d
	rs.XPerThread = rs.X / k
	rs.MeanTauNs = float64(sumTau) / float64(rs.N)
	rs.MeanWaitNs = float64(rs.SumWaitNs) / float64(rs.N)
	rs.W = float64(rs.SumWaitNs) / 1e9 / (k * d)
	mu := float64(rs.N) / k
	var ss float64
	for _, c := range counts {
		ss += (c - mu) * (c - mu)
	}
	if mu > 0 {
		rs.Cv = math.Sqrt(ss/k) / mu
	}
	tau := gather(recs, func(r *rec) *col { return &r.tau })
	slices.Sort(tau)
	rs.P50Ns, rs.P90Ns, rs.P99Ns, rs.P999Ns = quantile(tau, 0.5), quantile(tau, 0.9), quantile(tau, 0.99), quantile(tau, 0.999)
	rs.HistTau = histogram(tau)
	tau = nil
	wt := gather(recs, func(r *rec) *col { return &r.wait })
	slices.Sort(wt)
	rs.WaitP50Ns, rs.WaitP99Ns = quantile(wt, 0.5), quantile(wt, 0.99)
	rs.HistWait = histogram(wt)
	return rs
}
