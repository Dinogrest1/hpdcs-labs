// Режим створення задач (ЛР 4): -mode tasks.
//
// Генератор надсилає POST /<ресурс>/{id}/tasks у відкритому контурі з постійною інтенсивністю:
// момент k-го запиту start + k/rate обчислюється наперед, кожен запит іде в окремій горутині.
// id запису — випадковий з -ids (сто попередньо занесених записів). Ідентифікатор кожної створеної
// задачі береться з тіла відповіді 202 і записується у <out>.tasks.csv.
//
//	-count N        створити рівно N задач (крок 3: 300 задач на 100 за секунду)
//	-dur T          або створювати задачі T секунд
//	-priorities     Д1: пріоритети через кому, призначаються по колу (1,2,3 — порівну)
//
// Вихід: <out>.tasks.csv (кожен запит), <out>.seconds.csv (посекундно), <out>.json (підсумок).
package main

import (
	"encoding/json"
	"errors"
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

type taskReq struct {
	sched    time.Time
	done     time.Time
	recordID int
	priority int // 0 — тіло не надсилалось, сервіс ставить пріоритет за замовчуванням
	code     int // 0 — помилка зʼєднання, -1 — тайм-аут
	taskID   int64
}

func runTasks(urlTpl string, ids []int, rate float64, count, dur int, prioArg string,
	timeout time.Duration, out, label string) {
	var prios []int
	for _, p := range strings.Split(prioArg, ",") {
		if v, err := strconv.Atoi(strings.TrimSpace(p)); err == nil {
			prios = append(prios, v)
		}
	}
	n := count
	if n <= 0 {
		n = int(math.Floor(rate * float64(dur)))
	}
	client := &http.Client{Timeout: timeout, Transport: &http.Transport{
		DialContext:         (&net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConnsPerHost: 1024,
		IdleConnTimeout:     60 * time.Second,
		DisableCompression:  true,
	}}

	reqs := make([]taskReq, n)
	var wg sync.WaitGroup
	start := time.Now().Truncate(time.Second).Add(time.Second)
	time.Sleep(time.Until(start))
	for k := 0; k < n; k++ {
		r := &reqs[k]
		r.sched = start.Add(time.Duration(float64(k) / rate * float64(time.Second)))
		r.recordID = ids[rand.Intn(len(ids))]
		if len(prios) > 0 {
			r.priority = prios[k%len(prios)]
		}
		if d := time.Until(r.sched); d > 0 {
			time.Sleep(d)
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			u := strings.Replace(urlTpl, "{id}", strconv.Itoa(r.recordID), 1)
			var body io.Reader
			if r.priority > 0 {
				body = strings.NewReader(fmt.Sprintf(`{"priority":%d}`, r.priority))
			}
			req, _ := http.NewRequest(http.MethodPost, u, body)
			if body != nil {
				req.Header.Set("Content-Type", "application/json")
			}
			resp, err := client.Do(req)
			r.done = time.Now()
			if err != nil {
				var ne net.Error
				if errors.As(err, &ne) && ne.Timeout() {
					r.code = -1
				}
				return
			}
			defer resp.Body.Close()
			r.code = resp.StatusCode
			var t struct {
				ID int64 `json:"id"`
			}
			if b, e := io.ReadAll(resp.Body); e == nil && resp.StatusCode == http.StatusAccepted {
				json.Unmarshal(b, &t)
				r.taskID = t.ID
			}
		}()
	}
	wg.Wait()
	end := time.Now()

	// ---- кожен запит ----
	f, _ := os.Create(out + ".tasks.csv")
	fmt.Fprintln(f, "n,sent_unix,record_id,priority,code,task_id,latency_ms")
	type sec struct{ sent, created, non2xx, connErr int }
	secs := map[int64]*sec{}
	at := func(t time.Time) *sec {
		k := t.Unix()
		if secs[k] == nil {
			secs[k] = &sec{}
		}
		return secs[k]
	}
	var lat []float64
	created, non2xx, connErr := 0, 0, 0
	for k, r := range reqs {
		l := float64(r.done.Sub(r.sched).Microseconds()) / 1000
		fmt.Fprintf(f, "%d,%.3f,%d,%d,%d,%d,%.3f\n", k, float64(r.sched.UnixMicro())/1e6, r.recordID,
			r.priority, r.code, r.taskID, l)
		at(r.sched).sent++
		switch {
		case r.code == http.StatusAccepted:
			created++
			at(r.done).created++
			lat = append(lat, l)
		case r.code == 0:
			connErr++
			at(r.done).connErr++
		default:
			non2xx++
			at(r.done).non2xx++
		}
	}
	f.Close()

	// ---- посекундно ----
	f, _ = os.Create(out + ".seconds.csv")
	fmt.Fprintln(f, "t,unix,sent,created_202,non2xx,conn_errors")
	for u := start.Unix(); u <= end.Unix(); u++ {
		s := secs[u]
		if s == nil {
			s = &sec{}
		}
		fmt.Fprintf(f, "%d,%d,%d,%d,%d,%d\n", u-start.Unix(), u, s.sent, s.created, s.non2xx, s.connErr)
	}
	f.Close()

	sort.Float64s(lat)
	span := reqs[n-1].sched.Sub(reqs[0].sched).Seconds()
	actual := math.NaN()
	if span > 0 {
		actual = float64(n-1) / span
	}
	summary := map[string]any{
		"label": label, "mode": "tasks", "url": urlTpl, "rate": rate, "requested": n,
		"priorities": prios, "t0_unix": float64(start.UnixMicro()) / 1e6,
		"last_sent_unix": float64(reqs[n-1].sched.UnixMicro()) / 1e6,
		"end_unix":       float64(end.UnixMicro()) / 1e6,
		"created":        created, "non2xx": non2xx, "conn_errors": connErr,
		"sent_rate": jf(actual), "p50_ms": jf(pct(lat, .5)), "p99_ms": jf(pct(lat, .99)),
	}
	jfile, _ := os.Create(out + ".json")
	enc := json.NewEncoder(jfile)
	enc.SetIndent("", " ")
	enc.Encode(summary)
	jfile.Close()
	fmt.Printf("%s: надіслано %d POST за %.1f с (%.1f за секунду), створено задач (202): %d, не 2xx: %d, "+
		"помилок зʼєднання: %d, p99 відповіді %.1f мс\n", label, n, span, actual, created, non2xx, connErr, pct(lat, .99))
}
