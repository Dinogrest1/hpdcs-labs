package main

import (
	"sync"
	"time"
)

// Кільцевий буфер останніх Cap подій сервісу (структура С6).
// Синхронізація — лише sync.Mutex (глобальний замок) або sync.RWMutex; готових колекцій немає.

const (
	Cap   = 8192 // місткість буфера (Variant_parametr_3 для варіанта 26)
	LastN = 16   // скільки подій повертає last
)

// Event — подія сервісу. Seq присвоюється під замком при append і строго зростає.
// Sum — контрольна сума решти полів: якщо читач побачить напівзаписану подію, сума не зійдеться.
type Event struct {
	Seq    uint64 // номер події
	TimeNs int64  // момент створення, нс від початку програми (монотонний годинник)
	Kind   uint32 // тип події
	Code   uint32 // код події
	Sum    uint64 // контрольна сума
}

func checksum(e *Event) uint64 {
	return e.Seq*0x9E3779B97F4A7C15 ^ uint64(e.TimeNs)*0xC2B2AE3D27D4EB4F ^ uint64(e.Kind)<<32 ^ uint64(e.Code)
}

// Ring — сам буфер. Поля змінюються лише у Append під замком запису.
type Ring struct {
	mu    sync.Mutex   // глобальний замок (режим global)
	rw    sync.RWMutex // замок читання-запису (режим rw)
	useRW bool

	buf   [Cap]Event
	head  int    // індекс комірки, куди піде наступний запис
	count int    // кількість заповнених комірок, min(записів, Cap)
	seq   uint64 // номер останньої події = кількість append
}

func NewRing(useRW bool) *Ring { return &Ring{useRW: useRW} }

// Stamps — моменти однієї операції (нс від початку програми).
type Stamps struct {
	Call, Acq, Rel, Ret int64
	WantRel             bool // брати Rel (момент перед звільненням замка), лише в режимі cs
}

var t0 = time.Now()

// now — монотонний годинник: time.Since(t0) читає монотонну складову time.Now().
func now() int64 { return int64(time.Since(t0)) }

// Append записує подію в буфер.
func (r *Ring) Append(e Event, tm *Stamps) {
	tm.Call = now()
	if r.useRW {
		r.rw.Lock()
	} else {
		r.mu.Lock()
	}
	tm.Acq = now()

	r.seq++
	e.Seq = r.seq
	e.Sum = checksum(&e)
	r.buf[r.head] = e
	r.head++
	if r.head == Cap {
		r.head = 0
	}
	if r.count < Cap {
		r.count++
	}

	if tm.WantRel {
		tm.Rel = now()
	}
	if r.useRW {
		r.rw.Unlock()
	} else {
		r.mu.Unlock()
	}
	tm.Ret = now()
}

// Last копіює до LastN найновіших подій у dst (від новішої до старішої) і повертає їх кількість k та
// розмір буфера на момент читання. Метод нічого не присвоює полям Ring: лише читає їх під RLock
// і пише в буфер dst, який належить викликаючому потоку.
func (r *Ring) Last(dst *[LastN]Event, tm *Stamps) (k, size int) {
	tm.Call = now()
	if r.useRW {
		r.rw.RLock()
	} else {
		r.mu.Lock()
	}
	tm.Acq = now()

	size = r.count
	k = size
	if k > LastN {
		k = LastN
	}
	i := r.head
	for j := 0; j < k; j++ {
		i--
		if i < 0 {
			i += Cap
		}
		dst[j] = r.buf[i]
	}

	if tm.WantRel {
		tm.Rel = now()
	}
	if r.useRW {
		r.rw.RUnlock()
	} else {
		r.mu.Unlock()
	}
	tm.Ret = now()
	return
}

// checkLast перевіряє результат last поза замком. v1 — інваріант умови (не більше 16 подій,
// номери строго спадають); v2 — додаткові перевірки: k = min(16, size), номери йдуть без пропусків,
// контрольні суми, номер найновішої події не менший за той, що бачив цей самий потік раніше.
func checkLast(dst *[LastN]Event, k, size int, prevTop *uint64) (v1, v2 int) {
	if k > LastN {
		v1++
	}
	for j := 1; j < k; j++ {
		if dst[j].Seq >= dst[j-1].Seq {
			v1++
		}
		if dst[j].Seq+1 != dst[j-1].Seq {
			v2++
		}
	}
	want := size
	if want > LastN {
		want = LastN
	}
	if k != want {
		v2++
	}
	for j := 0; j < k; j++ {
		if checksum(&dst[j]) != dst[j].Sum {
			v2++
		}
	}
	if k > 0 {
		if dst[0].Seq < *prevTop {
			v2++
		}
		*prevTop = dst[0].Seq
	}
	return
}

// FinalCheck — перевірка кінцевого стану (потоки зупинено, замок не потрібен).
type FinalCheck struct {
	TotalAppends uint64 `json:"total_appends"`
	FinalSeq     uint64 `json:"final_seq"`
	Size         int    `json:"size"`
	ExpectedSize int    `json:"expected_size"`
	Last16Len    int    `json:"last16_len"`
	Last16Top    uint64 `json:"last16_top_seq"`
	Last16OK     bool   `json:"last16_ok"`
	ScanOK       bool   `json:"scan_ok"`
	SeqEqualsN   bool   `json:"seq_equals_appends"`
	SizeOK       bool   `json:"size_ok"`
	AllOK        bool   `json:"all_ok"`
}

func (r *Ring) Final(totalAppends uint64) FinalCheck {
	fc := FinalCheck{TotalAppends: totalAppends, FinalSeq: r.seq, Size: r.count}
	fc.ExpectedSize = Cap
	if totalAppends < Cap {
		fc.ExpectedSize = int(totalAppends)
	}
	fc.SeqEqualsN = fc.FinalSeq == totalAppends
	fc.SizeOK = fc.Size == fc.ExpectedSize
	var dst [LastN]Event
	var tm Stamps
	k, size := r.Last(&dst, &tm)
	var top uint64
	v1, v2 := checkLast(&dst, k, size, &top)
	fc.Last16Len = k
	fc.Last16Top = top
	fc.Last16OK = v1 == 0 && v2 == 0 && top == totalAppends
	// повний обхід від найстарішої комірки до найновішої: номери без пропусків і правильні суми
	fc.ScanOK = true
	start := r.head - r.count
	if start < 0 {
		start += Cap
	}
	var prev uint64
	for j := 0; j < r.count; j++ {
		e := &r.buf[(start+j)%Cap]
		if checksum(e) != e.Sum || (j > 0 && e.Seq != prev+1) {
			fc.ScanOK = false
		}
		prev = e.Seq
	}
	if r.count > 0 && prev != r.seq {
		fc.ScanOK = false
	}
	fc.AllOK = fc.SeqEqualsN && fc.SizeOK && fc.Last16OK && fc.ScanOK
	return fc
}
