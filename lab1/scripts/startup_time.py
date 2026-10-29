"""Час від `docker compose up -d` до першої 200 на /healthz (образи зібрані, томи порожні)."""
import statistics, subprocess, time, urllib.request

def healthy():
    try:
        with urllib.request.urlopen("http://localhost:8080/healthz", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False

times = []
for i in range(3):
    subprocess.run(["docker", "compose", "down", "-v"], capture_output=True)
    t0 = time.monotonic()
    p = subprocess.Popen(["docker", "compose", "up", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not healthy():
        time.sleep(0.05)
    dt = time.monotonic() - t0
    p.wait()
    times.append(dt)
    print(f"run {i+1}: {dt:.2f} s")
print(f"median: {statistics.median(times):.2f} s")
