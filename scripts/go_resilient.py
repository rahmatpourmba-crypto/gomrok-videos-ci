# -*- coding: utf-8 -*-
"""Render n-by-n with in-process watchdog and FS log. qN.mp4 deleted after send.
Usage: python go_resilient.py 171 172 173 ...   (whitelist by num)
State persisted to go_resilient.log each step.

Cross-platform: REMOTION_DIR / CHROME_BIN / GOMROK_TOKEN come from
batch_pipeline (env or defaults). Works on Windows and Linux CI."""
import sys, os, time, subprocess, json, asyncio, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import batch_pipeline as bp
from pathlib import Path

base = bp.REMOTION
c = base / "public" / "customs"
data = json.loads((Path(__file__).resolve().parent / "gomrok_final.json").read_text(encoding="utf-8"))
LOG = Path(os.environ.get("GOMROK_LOG", tempfile.gettempdir())) / "go_resilient.log"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_tts(q):
    n = q["num"]
    segs = bp.build_segments(q)
    for k in bp.SEG_ORDER:
        out = c / f"q{n}_{k}.mp3"
        if not out.exists() or out.stat().st_size == 0:
            log(f"TTS q{n} {k}")
            for retry in range(5):
                try:
                    asyncio.run(bp.gen(segs[k], out, bp.RATE))
                    break
                except Exception as e:
                    log(f"  TTS retry {retry}: {e}"); time.sleep(5)
    q["audio"] = {k: (bp.mp3_dur(c / f"q{n}_{k}.mp3") or 1.0) for k in bp.SEG_ORDER}
    (base / "out" / f"q{n}.json").write_text(
        json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")


def render_one(q):
    n = q["num"]
    mp4 = base / "out" / f"q{n}.mp4"
    if mp4.exists():
        mp4.unlink()
    props_file = base / "work" / f"props_q{n}.json"
    props_file.write_bytes(json.dumps({"q": q}, ensure_ascii=False, indent=1).encode("utf-8"))
    cmd = [bp.REMOTION_BIN, "render", str(bp.INDEX_TS), "CustomsQuestion", str(mp4),
           f"--props={props_file}", "--concurrency=1"]
    if bp.CHROME:
        cmd += ["--browser-executable", bp.CHROME]
    logfile = base / "work" / f"render_q{n}.log"
    for attempt in range(6):
        if mp4.exists():
            mp4.unlink()
        with open(logfile, "w", encoding="utf-8", errors="replace") as fh:
            p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(base),
                                 creationflags=CREATE_NO_WINDOW)
        stuck, last_m = 0, time.time()
        while p.poll() is None:
            time.sleep(10)
            m = os.path.getmtime(logfile)
            if m > last_m + 1:
                last_m, stuck = m, 0
            else:
                stuck += 1
            if stuck > 90:  # 15 min no log progress
                log(f"q{n} attempt {attempt} STUCK kill")
                p.kill(); p.wait(); break
        if mp4.exists() and mp4.stat().st_size > 0:
            log(f"q{n} rendered size={mp4.stat().st_size} attempt={attempt}")
            return True
        log(f"q{n} attempt {attempt} rc={p.returncode}")
    return False


def send_and_clean(q):
    n = q["num"]
    mp4 = base / "out" / f"q{n}.mp4"
    for i in range(15):
        try:
            res, _ = bp.send_telegram(q, mp4)
            if res != "FAILED" and '"ok":true' in res:
                log(f"q{n} SENT {res[:60]}")
                if mp4.exists():
                    mp4.unlink()
                    log(f"q{n} mp4 deleted")
                return True
        except Exception as e:
            log(f"q{n} send err {e}")
        time.sleep(8)
    log(f"q{n} send FAILED")
    return False


def main():
    nums = [int(x) for x in sys.argv[1:]]
    log("=== run nums=" + ",".join(map(str, nums)))
    for n in nums:
        key = str(n)
        if key not in data:
            log(f"q{n} missing in data"); continue
        q = dict(data[key])
        q["num"] = n
        if n in bp.BROKEN_QS:
            log(f"q{n} BROKEN skip"); continue
        if q.get("correct") is None:
            log(f"q{n} no correct skip"); continue
        opts = q.get("options") or []
        if len([o for o in opts if o.strip()]) < 4:
            log(f"q{n} <4 opts skip"); continue
        ensure_tts(q)
        if render_one(q):
            send_and_clean(q)


if __name__ == "__main__":
    main()