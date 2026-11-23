// Стенд ЛР 5: n потоків працюють з кільцевим буфером (С6). Непарні потоки пишуть, парні читають.
// Режими: run (n потоків), x1 (один потік по черзі пише і читає в пропорції n_w : n_r),
// cs (t_cs,w і t_cs,r одним потоком без конкуренції), clock (вартість читання годинника).
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"runtime"
	"slices"
	"sync"
	"time"
)

type thread struct {
	id        int
	role      string // "w", "r" або "alt"
	rec       [2]rec // 0 — запис, 1 — читання
	totalW    uint64 // усі append потоку, разом із прогрівом і операцією після кінця вікна
	totalR    uint64
	v1, v2    int
	lastRet   int64
	lastStamp Stamps
}

type ThreadOut struct {
	ID        int    `json:"id"`
	Role      string `json:"role"`
	Ops       uint64 `json:"ops"`
	TotalOps  uint64 `json:"total_ops_incl_warmup"`
	SumWaitNs int64  `json:"sum_wait_ns"`
	MaxWaitNs int64  `json:"max_wait_ns"`
	Viol      int    `json:"violations_last"`
	ViolExtra int    `json:"violations_extra"`
}

type Result struct {
	Mode        string      `json:"mode"`
	Lock        string      `json:"lock"`
	Run         int         `json:"run"`
	N           int         `json:"n"`
	Running     int         `json:"threads_running"`
	Nw          int         `json:"n_w"`
	Nr          int         `json:"n_r"`
	DSec        float64     `json:"d_s"`
	WarmSec     float64     `json:"warmup_s"`
	WindowSec   float64     `json:"window_actual_s"`
	GoVersion   string      `json:"go_version"`
	GOMAXPROCS  int         `json:"gomaxprocs"`
	NumCPU      int         `json:"num_cpu"`
	Threads     []ThreadOut `json:"threads"`
	Write       RoleStats   `json:"write"`
	Read        RoleStats   `json:"read"`
	X           float64     `json:"x_total"`
	PhiW        float64     `json:"phi_w"`
	PhiR        float64     `json:"phi_r"`
	Violations  int         `json:"violations_last_total"`
	ViolExtra   int         `json:"violations_extra_total"`
	Final       FinalCheck  `json:"final"`
	InvariantOK bool        `json:"invariants_ok"`
}

// xorshift64 — генератор аргументів кожного потоку (власний стан, без спільних даних).
type rng uint64

func (x *rng) next() uint64 {
	v := uint64(*x)
	v ^= v << 13
	v ^= v >> 7
	v ^= v << 17
	*x = rng(v)
	return v
}

func runThread(th *thread, r *Ring, startPtr, endPtr *int64, gate <-chan struct{}, wg *sync.WaitGroup, ratioW, ratioR int) {
	defer wg.Done()
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	rg := rng(0x9E3779B97F4A7C15 * uint64(th.id+1))
	var tm Stamps
	var dst [LastN]Event
	var prevTop uint64
	<-gate // start і end записані головною горутиною до close(gate)
	start, end := *startPtr, *endPtr
	pos := 0 // для режиму alt: позиція в циклі з ratioW записів і ratioR читань
	for {
		write := th.role == "w"
		if th.role == "alt" {
			write = pos < ratioW
			pos++
			if pos == ratioW+ratioR {
				pos = 0
			}
		}
		if write {
			v := rg.next() // аргументи генеруються в потоці до виклику операції
			e := Event{TimeNs: th.lastRet, Kind: uint32(v & 7), Code: uint32(v >> 32)}
			r.Append(e, &tm)
			th.totalW++
		} else {
			k, size := r.Last(&dst, &tm)
			th.totalR++
			a, b := checkLast(&dst, k, size, &prevTop)
			th.v1 += a
			th.v2 += b
		}
		th.lastRet = tm.Ret
		if tm.Call >= end {
			break
		}
		if tm.Call >= start {
			i := 1
			if write {
				i = 0
			}
			th.rec[i].add(tm.Ret-tm.Call, tm.Acq-tm.Call)
		}
	}
}

func runBench(mode, lock string, n int, dSec, warmSec float64, run int) *Result {
	useRW := lock == "rw"
	nw, nr := (n+1)/2, n/2
	r := NewRing(useRW)
	nt := n // кількість запущених потоків; в x1 — один
	if mode == "x1" {
		nt = 1
	}
	threads := make([]*thread, nt)
	gate := make(chan struct{})
	var wg sync.WaitGroup
	ratioW, ratioR := 1, 1
	if mode == "x1" {
		g := gcd(nw, nr)
		ratioW, ratioR = nw/g, nr/g
	}
	var start, end int64 // початок і кінець вимірювання, нс від t0
	for i := 0; i < nt; i++ {
		role := "r"
		if (i+1)%2 == 1 {
			role = "w"
		}
		if mode == "x1" {
			role = "alt"
		}
		threads[i] = &thread{id: i + 1, role: role}
	}
	startPtr, endPtr := &start, &end
	for _, th := range threads {
		wg.Add(1)
		go runThread(th, r, startPtr, endPtr, gate, &wg, ratioW, ratioR)
	}
	time.Sleep(200 * time.Millisecond) // дати потокам стати на брамі
	s := now() + int64(warmSec*1e9)
	*startPtr, *endPtr = s, s+int64(dSec*1e9)
	close(gate)
	wg.Wait()

	res := &Result{Mode: mode, Lock: lock, Run: run, N: n, Running: nt, Nw: nw, Nr: nr, DSec: dSec, WarmSec: warmSec,
		WindowSec: dSec, GoVersion: runtime.Version(), GOMAXPROCS: runtime.GOMAXPROCS(0), NumCPU: runtime.NumCPU()}
	var wrec, rrec []*rec
	var totalW uint64
	for _, th := range threads {
		totalW += th.totalW
		var ops uint64
		var sw, mw int64
		for i := range th.rec {
			ops += th.rec[i].n
			sw += th.rec[i].sumWait
			if th.rec[i].maxWait > mw {
				mw = th.rec[i].maxWait
			}
		}
		res.Threads = append(res.Threads, ThreadOut{ID: th.id, Role: th.role, Ops: ops, TotalOps: th.totalW + th.totalR,
			SumWaitNs: sw, MaxWaitNs: mw, Viol: th.v1, ViolExtra: th.v2})
		res.Violations += th.v1
		res.ViolExtra += th.v2
		if th.role == "w" || th.role == "alt" {
			wrec = append(wrec, &th.rec[0])
		}
		if th.role == "r" || th.role == "alt" {
			rrec = append(rrec, &th.rec[1])
		}
	}
	res.Write = roleStats("w", wrec, dSec)
	res.Read = roleStats("r", rrec, dSec)
	res.X = res.Write.X + res.Read.X
	if res.X > 0 {
		res.PhiW, res.PhiR = res.Write.X/res.X, res.Read.X/res.X
	}
	res.Final = r.Final(totalW)
	res.InvariantOK = res.Final.AllOK && res.Violations == 0 && res.ViolExtra == 0
	return res
}

func gcd(a, b int) int {
	for b != 0 {
		a, b = b, a%b
	}
	return a
}

func ms(ns int64) float64  { return float64(ns) / 1e6 }
func us(ns uint32) float64 { return float64(ns) / 1e3 }

func printResult(res *Result) {
	fmt.Printf("== режим %s, замок %s, n = %d (n_w = %d, n_r = %d), потоків запущено %d, D = %g с, прогрів %g с, прогін %d\n",
		res.Mode, res.Lock, res.N, res.Nw, res.Nr, res.Running, res.DSec, res.WarmSec, res.Run)
	fmt.Printf("   %s, GOMAXPROCS = %d, логічних ядер = %d\n", res.GoVersion, res.GOMAXPROCS, res.NumCPU)
	if res.Mode == "run" {
		fmt.Println("   потік  роль     операцій за D   найбільший t_wait, мс")
		for _, t := range res.Threads {
			role := "запис"
			if t.Role == "r" {
				role = "читання"
			}
			fmt.Printf("   %5d  %-7s %14d   %10.3f\n", t.ID, role, t.Ops, ms(t.MaxWaitNs))
		}
	}
	for _, rs := range []RoleStats{res.Write, res.Read} {
		name := "запис"
		if rs.Role == "r" {
			name = "читання"
		}
		fmt.Printf("   %-8s N = %d  X = %.0f оп/с  p50 = %.3f мкс  p99 = %.3f мкс  p999 = %.3f мкс  max τ = %.3f мс\n",
			name, rs.N, rs.X, us(rs.P50Ns), us(rs.P99Ns), us(rs.P999Ns), ms(rs.MaxTauNs))
		fmt.Printf("            найбільший t_wait = %.3f мс  w = %.4f  cv = %.4f  середній t_wait = %.0f нс\n",
			ms(rs.MaxWaitNs), rs.W, rs.Cv, rs.MeanWaitNs)
	}
	fmt.Printf("   X = %.0f оп/с  φ_w = %.4f  φ_r = %.4f\n", res.X, res.PhiW, res.PhiR)
	f := res.Final
	fmt.Printf("   інваріанти: порушень last (≤16, строго спадають) = %d, додаткових (без пропусків, суми, k = min(16, розмір), монотонність) = %d\n",
		res.Violations, res.ViolExtra)
	fmt.Printf("   після прогону: усього append = %d, останній seq = %d (збігаються: %v), розмір = %d (очікується %d: %v)\n",
		f.TotalAppends, f.FinalSeq, f.SeqEqualsN, f.Size, f.ExpectedSize, f.SizeOK)
	fmt.Printf("   last(16) з кінцевого стану: %d подій, найновіша seq = %d, коректно: %v; повний обхід буфера коректний: %v\n",
		f.Last16Len, f.Last16Top, f.Last16OK, f.ScanOK)
	fmt.Printf("   ІНВАРІАНТИ: %s\n", map[bool]string{true: "виконано", false: "ПОРУШЕНО"}[res.InvariantOK])
}

// ---- режим cs: t_cs,w і t_cs,r одним потоком без конкуренції ----

type CSResult struct {
	Mode       string  `json:"mode"`
	Lock       string  `json:"lock"`
	Run        int     `json:"run"`
	Ops        int     `json:"ops_per_role"`
	WMeanNs    float64 `json:"t_cs_w_mean_ns"`
	WMedianNs  float64 `json:"t_cs_w_median_ns"`
	RMeanNs    float64 `json:"t_cs_r_mean_ns"`
	RMedianNs  float64 `json:"t_cs_r_median_ns"`
	WTauMeanNs float64 `json:"tau_w_mean_ns"`
	RTauMeanNs float64 `json:"tau_r_mean_ns"`
	ClockNs    float64 `json:"clock_ns"`
	GoVersion  string  `json:"go_version"`
}

func runCS(lock string, ops, run int) *CSResult {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	r := NewRing(lock == "rw")
	var tm Stamps
	tm.WantRel = true
	var rg rng = 12345
	w := make([]uint32, 0, ops)
	rd := make([]uint32, 0, ops)
	var sumTauW, sumTauR float64
	for i := 0; i < ops+100000; i++ { // 100000 операцій прогріву
		v := rg.next()
		r.Append(Event{Kind: uint32(v & 7), Code: uint32(v >> 32)}, &tm)
		if i >= 100000 {
			w = append(w, uint32(tm.Rel-tm.Acq))
			sumTauW += float64(tm.Ret - tm.Call)
		}
	}
	var dst [LastN]Event
	for i := 0; i < ops+100000; i++ {
		r.Last(&dst, &tm)
		if i >= 100000 {
			rd = append(rd, uint32(tm.Rel-tm.Acq))
			sumTauR += float64(tm.Ret - tm.Call)
		}
	}
	mean := func(x []uint32) float64 {
		var s float64
		for _, v := range x {
			s += float64(v)
		}
		return s / float64(len(x))
	}
	med := func(x []uint32) float64 { slices.Sort(x); return float64(x[len(x)/2]) }
	return &CSResult{Mode: "cs", Lock: lock, Run: run, Ops: ops, WMeanNs: mean(w), WMedianNs: med(w),
		RMeanNs: mean(rd), RMedianNs: med(rd), WTauMeanNs: sumTauW / float64(ops), RTauMeanNs: sumTauR / float64(ops),
		GoVersion: runtime.Version()}
}

// ---- режим clock: вартість вимірювання часу ----

type ClockResult struct {
	Mode          string  `json:"mode"`
	NowSinceNs    float64 `json:"time_since_ns"`
	TimeNowNs     float64 `json:"time_now_ns"`
	StampsPerOpNs float64 `json:"three_stamps_ns"`
	Reps          int     `json:"reps"`
}

var sink int64

func runClock(reps int) *ClockResult {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	best := func(f func() int64) float64 {
		b := math.MaxFloat64
		for k := 0; k < 5; k++ {
			s := time.Now()
			sink += f()
			d := float64(time.Since(s).Nanoseconds()) / float64(reps)
			if d < b {
				b = d
			}
		}
		return b
	}
	loop := best(func() int64 {
		var a int64
		for i := 0; i < reps; i++ {
			a += int64(i)
		}
		return a
	})
	since := best(func() int64 {
		var a int64
		for i := 0; i < reps; i++ {
			a += now()
		}
		return a
	})
	nowc := best(func() int64 {
		var a int64
		for i := 0; i < reps; i++ {
			a += time.Now().UnixNano() & 1
		}
		return a
	})
	return &ClockResult{Mode: "clock", NowSinceNs: since - loop, TimeNowNs: nowc - loop, StampsPerOpNs: 3 * (since - loop), Reps: reps}
}

func writeJSON(path string, v any) {
	if path == "" {
		return
	}
	b, err := json.MarshalIndent(v, "", " ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.WriteFile(path, b, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func main() {
	mode := flag.String("mode", "run", "run | x1 | cs | clock")
	lock := flag.String("lock", "global", "global | rw")
	n := flag.Int("n", 12, "кількість потоків")
	d := flag.Float64("d", 10, "тривалість вимірювання D, с")
	warm := flag.Float64("warm", 1, "прогрів, с")
	run := flag.Int("run", 1, "номер повторення")
	ops := flag.Int("ops", 5_000_000, "операцій кожної ролі в режимі cs")
	out := flag.String("out", "", "файл JSON з повним результатом")
	flag.Parse()
	if *lock != "global" && *lock != "rw" {
		fmt.Fprintln(os.Stderr, "-lock: global або rw")
		os.Exit(2)
	}
	switch *mode {
	case "run", "x1":
		res := runBench(*mode, *lock, *n, *d, *warm, *run)
		printResult(res)
		writeJSON(*out, res)
		if !res.InvariantOK {
			os.Exit(1)
		}
	case "cs":
		c := runCS(*lock, *ops, *run)
		c.ClockNs = runClock(20_000_000).NowSinceNs
		fmt.Printf("== режим cs, замок %s, прогін %d, %d операцій кожної ролі, %s\n", c.Lock, c.Run, c.Ops, c.GoVersion)
		fmt.Printf("   t_cs,w: середній %.1f нс, медіана %.1f нс; τ запису (від виклику до повернення) середня %.1f нс\n", c.WMeanNs, c.WMedianNs, c.WTauMeanNs)
		fmt.Printf("   t_cs,r: середній %.1f нс, медіана %.1f нс; τ читання середня %.1f нс\n", c.RMeanNs, c.RMedianNs, c.RTauMeanNs)
		writeJSON(*out, c)
	case "clock":
		c := runClock(20_000_000)
		fmt.Printf("== режим clock: вартість одного виклику, нс (мінімум із 5 серій по %d, мінус порожній цикл)\n", c.Reps)
		fmt.Printf("   time.Since(t0) (монотонний годинник, використовується стендом): %.1f нс\n", c.NowSinceNs)
		fmt.Printf("   time.Now() з перетворенням у UnixNano (стінний + монотонний): %.1f нс\n", c.TimeNowNs)
		fmt.Printf("   три позначки часу на одну операцію (t_call, t_acq, t_ret): %.1f нс\n", c.StampsPerOpNs)
		writeJSON(*out, c)
	default:
		fmt.Fprintln(os.Stderr, "невідомий режим")
		os.Exit(2)
	}
}
