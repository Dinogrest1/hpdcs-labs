import sys, json
d = json.load(sys.stdin)
print(sys.argv[1], d["target_rps"] or d["concurrency"], round(d["achieved_rps"]), "p50=%.1f p95=%.1f p99=%.1f" % (d["p50_ms"], d["p95_ms"], d["p99_ms"]),
      "non2xx=%.3f L=%.1f lag=%.1fms" % (d["non2xx_share"], d["inflight_mean"], d["generator_lag_max_ms"]))
