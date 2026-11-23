package main

import (
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Послідовна коректність: розмір, номери, порядок last.
func TestSequential(t *testing.T) {
	for _, rw := range []bool{false, true} {
		r := NewRing(rw)
		var tm Stamps
		var dst [LastN]Event
		if k, size := r.Last(&dst, &tm); k != 0 || size != 0 {
			t.Fatalf("порожній буфер: k=%d size=%d", k, size)
		}
		for i := 1; i <= 3*Cap+5; i++ {
			r.Append(Event{Kind: uint32(i & 7), Code: uint32(i)}, &tm)
			k, size := r.Last(&dst, &tm)
			wantSize := min(i, Cap)
			if size != wantSize || k != min(LastN, wantSize) {
				t.Fatalf("i=%d: k=%d size=%d", i, k, size)
			}
			for j := 0; j < k; j++ {
				if dst[j].Seq != uint64(i-j) {
					t.Fatalf("i=%d j=%d: seq=%d", i, j, dst[j].Seq)
				}
			}
		}
		if fc := r.Final(uint64(3*Cap + 5)); !fc.AllOK {
			t.Fatalf("Final: %+v", fc)
		}
	}
}

// Читання не змінює поля структури: копія до і після мільйона last() в одному потоці.
func TestLastDoesNotMutate(t *testing.T) {
	for _, rw := range []bool{false, true} {
		r := NewRing(rw)
		var tm Stamps
		for i := 0; i < 20000; i++ {
			r.Append(Event{Kind: uint32(i), Code: uint32(i * 3)}, &tm)
		}
		bufBefore := r.buf // копія масиву
		headB, countB, seqB := r.head, r.count, r.seq
		var dst [LastN]Event
		for i := 0; i < 1_000_000; i++ {
			r.Last(&dst, &tm)
		}
		if r.buf != bufBefore || r.head != headB || r.count != countB || r.seq != seqB {
			t.Fatalf("last() змінила структуру (rw=%v)", rw)
		}
	}
}

// Одночасні читачі і писці (запускати з -race): жодного порушення інваріантів.
func TestConcurrent(t *testing.T) {
	for _, rw := range []bool{false, true} {
		r := NewRing(rw)
		var wg sync.WaitGroup
		var stop atomic.Bool
		var viol atomic.Int64
		var total atomic.Uint64
		for w := 0; w < 6; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				var tm Stamps
				var n uint64
				for !stop.Load() {
					r.Append(Event{Kind: 1, Code: 2}, &tm)
					n++
				}
				total.Add(n)
			}()
		}
		for q := 0; q < 6; q++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				var tm Stamps
				var dst [LastN]Event
				var top uint64
				for !stop.Load() {
					k, size := r.Last(&dst, &tm)
					a, b := checkLast(&dst, k, size, &top)
					viol.Add(int64(a + b))
				}
			}()
		}
		time.Sleep(500 * time.Millisecond)
		stop.Store(true)
		wg.Wait()
		if viol.Load() != 0 {
			t.Fatalf("rw=%v: порушень %d", rw, viol.Load())
		}
		if fc := r.Final(total.Load()); !fc.AllOK {
			t.Fatalf("rw=%v: Final %+v", rw, fc)
		}
		t.Logf("rw=%v: append = %d", rw, total.Load())
	}
}
