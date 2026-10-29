// Генератор навантаження з фіксованою швидкістю надходження (відкритий контур).
//
// Момент надсилання k-го запиту відрізка обчислюється наперед: start + k/rate.
// Кожен запит виконується в окремій горутині, тож планувальник не чекає відповіді,
// а кількість одночасних запитів не обмежена пулом: фактична інтенсивність
// надсилання дорівнює заданій. Затримка рахується від запланованого моменту.
//
// Профілі:
//
//	-mode step     прогрів, далі сходинки start, start+inc, ... до правила зупинки
//	-mode profile  довільні відрізки "тривалість:інтенсивність,..." (напр. "30:300,60:900,90:300")
//
// Модель зʼєднань (ЛР 3): -conns K тримає K постійних keep-alive зʼєднань, кожне закривається
// після -per-conn запитів і замінюється новим. Запит, для якого немає вільного зʼєднання, чекає,
// і це очікування входить у затримку. -conns 0 — без обмеження (як у ЛР 2).
//
// Вихід: <out>.seconds.csv (посекундні величини), <out>.json (параметри і підсумок сходинок).
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type result struct {
	done    time.Time // момент отримання відповіді або помилки
	latency float64   // мс від запланованого моменту надсилання
	code    int       // HTTP-код; 0 — помилка зʼєднання; -1 — тайм-аут
}

type second struct {
	Target   float64   // задана інтенсивність у цю секунду
	Sent     int       // фактично надіслано в цю секунду
	OK       int       // відповіді 2xx, завершені в цю секунду (= досягнута інтенсивність)
	Non2xx   int       // відповіді не 2xx, включно з тайм-аутами
	ConnErr  int       // помилки зʼєднання
	Timeouts int       // з них тайм-аутів (уже пораховані в Non2xx)
	lat      []float64 // затримки запитів, завершених у цю секунду (до стиснення)
	hist     map[int32]int32
	p        [3]float64 // точні p50, p95, p99 секунди, обчислені перед стисненням
	packed   bool
}

// Логарифмічні бакети з кроком 0.5%: для довгих прогонів секунда стискається в гістограму,
// і перцентиль сходинки береться з об'єднаних гістограм з відносною похибкою < 0.25%.
const histBase = 1.005

func hb(ms float64) int32 {
	if ms < 0.001 {
		ms = 0.001
	}
	return int32(math.Floor(math.Log(ms) / math.Log(histBase)))
}

// Середина бакета в мс (геометрична).
func hv(b int32) float64 { return math.Pow(histBase, float64(b)+0.5) }

func (s *second) pack() {
	if s.packed {
		return
	}
	sort.Float64s(s.lat)
	s.p = [3]float64{pct(s.lat, .5), pct(s.lat, .95), pct(s.lat, .99)}
	s.hist = make(map[int32]int32)
	for _, x := range s.lat {
		s.hist[hb(x)]++
	}
	s.lat = nil
	s.packed = true
}

func histPct(h map[int32]int32, p float64) float64 {
	var n int64
	keys := make([]int32, 0, len(h))
	for k, c := range h {
		keys = append(keys, k)
		n += int64(c)
	}
	if n == 0 {
		return math.NaN()
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	rank := int64(math.Ceil(p * float64(n)))
	var c int64
	for _, k := range keys {
		c += int64(h[k])
		if c >= rank {
			return hv(k)
		}
	}
	return hv(keys[len(keys)-1])
}

type segment struct {
	Name  string  `json:"name"`
	Dur   int     `json:"duration_s"`
	Rate  float64 `json:"rate"`
	Start int     `json:"start_s"` // секунда від початку прогону
}

// jf — число для JSON: NaN (сходинка без жодної відповіді) записується як null, а не ламає файл.
type jf float64

func (v jf) MarshalJSON() ([]byte, error) {
	if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
		return []byte("null"), nil
	}
	return json.Marshal(float64(v))
}

type stepSummary struct {
	Step        int     `json:"step"`
	Target      float64 `json:"target_rps"`
	StartUnix   float64 `json:"start_unix"`
	WindowFrom  float64 `json:"window_from_unix"` // останні 15 с сходинки
	WindowTo    float64 `json:"window_to_unix"`
	Achieved    jf      `json:"achieved_rps"` // середня досягнута за вікно
	AchievedStd jf      `json:"achieved_std"`
	SentRate    jf      `json:"sent_rps"`
	P50         jf      `json:"p50_ms"`
	P95         jf      `json:"p95_ms"`
	P99         jf      `json:"p99_ms"`
	Non2xxShare jf      `json:"non2xx_share"`
	ConnErr     int     `json:"conn_errors"`
}

var (
	mu      sync.Mutex
	seconds []*second
	t0      time.Time
)

func sec(t time.Time) *second {
	i := int(t.Sub(t0) / time.Second)
	if i < 0 {
		i = 0
	}
	for len(seconds) <= i {
		seconds = append(seconds, &second{})
	}
	return seconds[i]
}

func pct(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return math.NaN()
	}
	k := int(math.Ceil(p*float64(len(sorted)))) - 1
	if k < 0 {
		k = 0
	}
	return sorted[k]
}

func parseIDs(s string) []int {
	var ids []int
	for _, part := range strings.Split(s, ",") {
		if a, b, ok := strings.Cut(part, "-"); ok {
			lo, _ := strconv.Atoi(a)
			hi, _ := strconv.Atoi(b)
			for i := lo; i <= hi; i++ {
				ids = append(ids, i)
			}
		} else if part != "" {
			i, _ := strconv.Atoi(part)
			ids = append(ids, i)
		}
	}
	return ids
}

func main() {
	url := flag.String("url", "http://lb/telemed-sessions/{id}", "шаблон URL, {id} замінюється на випадковий id")
	idsArg := flag.String("ids", "1-100", "діапазон ідентифікаторів")
	mode := flag.String("mode", "step", "step | profile")
	warmRate := flag.Float64("warmup-rate", 200, "інтенсивність прогріву")
	warmDur := flag.Int("warmup", 10, "тривалість прогріву, с")
	startRate := flag.Float64("start", 200, "перша сходинка, запитів/с")
	inc := flag.Float64("inc", 200, "приріст сходинки, запитів/с")
	stepDur := flag.Int("step-dur", 20, "тривалість сходинки, с")
	maxRate := flag.Float64("G", 0, "межа генератора: зупинитись після сходинки з заданою >= G (0 — без межі)")
	maxSteps := flag.Int("max-steps", 400, "запобіжник кількості сходинок")
	profile := flag.String("profile", "", "відрізки тривалість:інтенсивність через кому (mode=profile)")
	timeout := flag.Duration("timeout", 5*time.Second, "тайм-аут запиту")
	out := flag.String("out", "/results/run", "префікс вихідних файлів")
	label := flag.String("label", "", "мітка прогону")
	conns := flag.Int("conns", 0, "кількість постійних зʼєднань (0 — без обмеження)")
	perConn := flag.Int("per-conn", 100, "запитів на одне зʼєднання до його заміни")
	win := flag.Int("window", 15, "останні секунди сходинки, за якими рахуються середні")
	flag.Parse()

	ids := parseIDs(*idsArg)
	tr := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: *timeout, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:        0,
		MaxIdleConnsPerHost: 20000,
		IdleConnTimeout:     60 * time.Second,
		DisableCompression:  true,
	}
	client := &http.Client{Transport: tr, Timeout: *timeout}

	// Слот = одне зʼєднання: власний Transport з єдиним зʼєднанням і лічильник запитів.
	type slot struct {
		c *http.Client
		n int
	}
	newSlot := func() *slot {
		t := &http.Transport{
			DialContext:         (&net.Dialer{Timeout: *timeout, KeepAlive: 30 * time.Second}).DialContext,
			MaxConnsPerHost:     1,
			MaxIdleConnsPerHost: 1,
			IdleConnTimeout:     60 * time.Second,
			DisableCompression:  true,
		}
		return &slot{c: &http.Client{Transport: t, Timeout: *timeout}}
	}
	var slots chan *slot
	if *conns > 0 {
		slots = make(chan *slot, *conns)
		for i := 0; i < *conns; i++ {
			slots <- newSlot()
		}
	}

	results := make(chan result, 1<<16)
	var collector sync.WaitGroup
	collector.Add(1)
	go func() {
		defer collector.Done()
		for r := range results {
			mu.Lock()
			s := sec(r.done)
			switch {
			case r.code == 0:
				s.ConnErr++
			case r.code == -1:
				s.Non2xx++
				s.Timeouts++
			case r.code >= 200 && r.code < 300:
				s.OK++
			default:
				s.Non2xx++
			}
			s.lat = append(s.lat, r.latency)
			mu.Unlock()
		}
	}()

	// Секунди, старші за тайм-аут + 2 с, уже не отримають нових відповідей — стискаємо їх.
	stopPack := make(chan struct{})
	go func() {
		tk := time.NewTicker(time.Second)
		defer tk.Stop()
		for {
			select {
			case <-stopPack:
				return
			case now := <-tk.C:
				limit := int(now.Sub(t0)/time.Second) - int(timeout.Seconds()) - 2
				mu.Lock()
				for i := 0; i < limit && i < len(seconds); i++ {
					seconds[i].pack()
				}
				mu.Unlock()
			}
		}
	}()

	var inflight sync.WaitGroup
	fire := func(sched time.Time) {
		defer inflight.Done()
		u := strings.Replace(*url, "{id}", strconv.Itoa(ids[rand.Intn(len(ids))]), 1)
		code := 0
		c := client
		var sl *slot
		if slots != nil {
			sl = <-slots // чекаємо вільне зʼєднання; час очікування входить у затримку
			c = sl.c
		}
		resp, err := c.Get(u)
		if err == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			code = resp.StatusCode
		} else {
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				code = -1
			}
		}
		if sl != nil {
			sl.n++
			if sl.n >= *perConn || err != nil {
				// після 100 запитів (або помилки) зʼєднання закривається і замінюється новим
				sl.c.Transport.(*http.Transport).CloseIdleConnections()
				sl = newSlot()
			}
			slots <- sl
		}
		now := time.Now()
		results <- result{done: now, latency: float64(now.Sub(sched).Microseconds()) / 1000, code: code}
	}

	// Надсилає відрізок з постійною інтенсивністю; моменти — від початку відрізка.
	runSegment := func(segStart time.Time, dur int, rate float64) {
		mu.Lock()
		for i := 0; i < dur; i++ {
			sec(segStart.Add(time.Duration(i)*time.Second + time.Millisecond)).Target = rate
		}
		mu.Unlock()
		n := int(math.Floor(rate * float64(dur)))
		for k := 0; k < n; k++ {
			sched := segStart.Add(time.Duration(float64(k) / rate * float64(time.Second)))
			if d := time.Until(sched); d > 0 {
				time.Sleep(d)
			}
			mu.Lock()
			sec(time.Now()).Sent++
			mu.Unlock()
			inflight.Add(1)
			go fire(sched)
		}
		if d := time.Until(segStart.Add(time.Duration(dur) * time.Second)); d > 0 {
			time.Sleep(d)
		}
	}

	// Середня досягнута і розкид за останні -window секунд сходинки (решта — перехідні).
	window := func(seg segment) (float64, float64, int, int) {
		mu.Lock()
		defer mu.Unlock()
		var v []float64
		from := seg.Start + seg.Dur - *win
		if from < seg.Start {
			from = seg.Start
		}
		for i := from; i < seg.Start+seg.Dur; i++ {
			v = append(v, float64(sec(t0.Add(time.Duration(i)*time.Second)).OK))
		}
		var m float64
		for _, x := range v {
			m += x
		}
		m /= float64(len(v))
		var sd float64
		for _, x := range v {
			sd += (x - m) * (x - m)
		}
		if len(v) > 1 {
			sd = math.Sqrt(sd / float64(len(v)-1))
		}
		return m, sd, from, seg.Start + seg.Dur
	}

	t0 = time.Now().Truncate(time.Second).Add(time.Second)
	time.Sleep(time.Until(t0))
	var segs []segment
	cur := 0
	add := func(name string, dur int, rate float64) segment {
		s := segment{Name: name, Dur: dur, Rate: rate, Start: cur}
		runSegment(t0.Add(time.Duration(cur)*time.Second), dur, rate)
		cur += dur
		segs = append(segs, s)
		return s
	}

	stopReason := ""
	switch *mode {
	case "step":
		if *warmDur > 0 {
			add("warmup", *warmDur, *warmRate)
		}
		below := 0
		for i := 0; i < *maxSteps; i++ {
			rate := *startRate + float64(i)*(*inc)
			s := add(fmt.Sprintf("step%d", i+1), *stepDur, rate)
			m, _, _, _ := window(s)
			fmt.Fprintf(os.Stderr, "step %d target=%.0f achieved=%.1f\n", i+1, rate, m)
			if m < 0.9*rate {
				below++
			} else {
				below = 0
			}
			if below >= 2 {
				stopReason = "дві сходинки поспіль досягнута < 0.9 заданої"
				break
			}
			if *maxRate > 0 && rate >= *maxRate {
				stopReason = "сходинка досягла межі генератора G"
				break
			}
		}
		if stopReason == "" {
			stopReason = "max-steps"
		}
	case "profile":
		for i, part := range strings.Split(*profile, ",") {
			d, r, _ := strings.Cut(part, ":")
			dur, _ := strconv.Atoi(d)
			rate, _ := strconv.ParseFloat(r, 64)
			add(fmt.Sprintf("seg%d", i+1), dur, rate)
		}
		stopReason = "кінець профілю"
	}
	inflight.Wait()
	close(results)
	collector.Wait()
	close(stopPack)
	for _, s := range seconds {
		s.pack()
	}

	// ---- Посекундний CSV ----
	f, _ := os.Create(*out + ".seconds.csv")
	fmt.Fprintln(f, "t,unix,target,sent,achieved_2xx,non2xx,conn_errors,timeouts,p50_ms,p95_ms,p99_ms")
	for i, s := range seconds {
		fmt.Fprintf(f, "%d,%d,%.0f,%d,%d,%d,%d,%d,%.3f,%.3f,%.3f\n", i, t0.Unix()+int64(i), s.Target, s.Sent,
			s.OK, s.Non2xx, s.ConnErr, s.Timeouts, s.p[0], s.p[1], s.p[2])
	}
	f.Close()

	// ---- Підсумок сходинок ----
	var steps []stepSummary
	for _, sg := range segs {
		if sg.Name == "warmup" {
			continue
		}
		m, sd, from, to := window(sg)
		lat := map[int32]int32{}
		sent, ok, bad, ce := 0, 0, 0, 0
		for i := from; i < to && i < len(seconds); i++ {
			s := seconds[i]
			for k, c := range s.hist {
				lat[k] += c
			}
			sent += s.Sent
			ok += s.OK
			bad += s.Non2xx
			ce += s.ConnErr
		}
		share := 0.0
		if ok+bad+ce > 0 {
			share = float64(bad+ce) / float64(ok+bad+ce)
		}
		steps = append(steps, stepSummary{
			Step: len(steps) + 1, Target: sg.Rate,
			StartUnix:  float64(t0.Unix() + int64(sg.Start)),
			WindowFrom: float64(t0.Unix() + int64(from)), WindowTo: float64(t0.Unix() + int64(to)),
			Achieved: jf(m), AchievedStd: jf(sd), SentRate: jf(float64(sent) / float64(to-from)),
			P50: jf(histPct(lat, .5)), P95: jf(histPct(lat, .95)), P99: jf(histPct(lat, .99)), Non2xxShare: jf(share), ConnErr: ce,
		})
	}
	summary := map[string]any{
		"label": *label, "mode": *mode, "url": *url, "timeout_s": timeout.Seconds(),
		"t0_unix": t0.Unix(), "end_unix": t0.Unix() + int64(cur), "segments": segs,
		"stop_reason": stopReason, "steps": steps,
	}
	jfile, _ := os.Create(*out + ".json")
	enc := json.NewEncoder(jfile)
	enc.SetIndent("", " ")
	enc.Encode(summary)
	jfile.Close()

	best := stepSummary{}
	for _, s := range steps {
		if s.Achieved > best.Achieved {
			best = s
		}
	}
	fmt.Printf("%s: %d сходинок, зупинка: %s; X = %.1f ± %.1f на сходинці %d (задано %.0f), p99 = %.1f мс\n",
		*label, len(steps), stopReason, best.Achieved, best.AchievedStd, best.Step, best.Target, best.P99)
}
