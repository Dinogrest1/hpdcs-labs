| Величина | Значення, мс | Звідки |
|---|---|---|
| задач | 20 | `worker_task_duration_seconds_count{step="process"}` |
| fetch (GET запису від web) | 0.63 | `_sum / _count`, step="fetch" |
| store (завершення і запис результату) | 0.82 | step="store" |
| **t_io** = fetch + store | **1.45** |  |
| **t_cpu** = process | **12.75** | step="process" |
| захоплення з задачею (claim) | 0.36 | step="claim" |
| порожні захоплення за прогін | 19 | `worker_claims_total{result="empty"}` |
