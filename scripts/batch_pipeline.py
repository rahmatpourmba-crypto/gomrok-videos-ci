# -*- coding: utf-8 -*-
"""
Batch pipeline for the Gomrok customs-broker exam videos.

For each selected question N (from gomrok_final.json):
  1. writes data file out/qN.json (template + measured audio durations)
  2. generates 7 TTS segments into <remotion>/public/customs/qN_{q,o1..o4,a,r}.mp3
  3. renders <remotion>/out/qN.mp4 via the Remotion CLI (CustomsQuestion comp)
  4. (optional --send) posts the video + caption to the Telegram channel

Usage:
  python batch_pipeline.py [--range A-B] [--list 1,5,9] [--send] [--dry-run]

Cross-platform (works on Windows locally and on Linux CI runners):
  * REMOTION_DIR       -> path to the Remotion project (default: local remotion-video)
  * CHROME_BIN         -> chrome/chromium executable (default: Windows Chrome, else headless)
  * GOMROK_TOKEN       -> Telegram bot token (default: ~/.gomrok_token file)
"""
import sys, io, os, json, asyncio, subprocess, time, re, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(opener)

HERE = Path(__file__).resolve().parent
FIN = HERE / "gomrok_final.json"
REMOTION = Path(os.environ.get("REMOTION_DIR", r"C:\Users\Admin\Documents\Default Project\remotion-video"))
INDEX_TS = REMOTION / "src" / "index.ts"
CUSTOMS_DIR = REMOTION / "public" / "customs"
OUT = REMOTION / "out"
WORK = REMOTION / "work"
FPS = 30
VOICE = "fa-IR-FaridNeural"
RATE = "-8%"
SEG_ORDER = ["q", "o1", "o2", "o3", "o4", "a", "r"]
NUMWORDS = {1: "یک", 2: "دو", 3: "سه", 4: "چهار"}


def resolve_chrome():
    e = os.environ.get("CHROME_BIN")
    if e:
        return e
    c = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    return str(c) if c.exists() else ""


CHROME = resolve_chrome()


def resolve_remotion_bin():
    bin_dir = REMOTION / "node_modules" / ".bin"
    if os.name == "nt":
        cmd = bin_dir / "remotion.cmd"
    else:
        cmd = bin_dir / "remotion"
    if cmd.exists():
        return str(cmd)
    if os.name == "nt":
        alt = bin_dir / "remotion"
    else:
        alt = bin_dir / "remotion.cmd"
    return str(alt if alt.exists() else cmd)


REMOTION_BIN = resolve_remotion_bin()


def resolve_token():
    t = os.environ.get("GOMROK_TOKEN")
    if t:
        return t
    for p in (Path.home() / ".gomrok_token", HERE / ".token"):
        try:
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
        except Exception:
            pass
    return ""


TOKEN = resolve_token()
CHAT = "-1004335313399"
API = f"https://api.telegram.org/bot{TOKEN}"

FA_CLEAN = {"ك": "ک", "ي": "ی", "ة": "ه", "ی": "ی"}
COMPOUNDS = {"کالا": "کالا", "کالای": "کالای", "کالاهای": "کالاهای",
             "کالاها": "کالاها", "اقلام": "اقلام"}
PRONOUNCE = {}

FA_REPL = {ord("ك"): "ک", ord("ي"): "ی", ord("ة"): "ه"}


def fa_clean(t):
    if not t:
        return t
    s = str(t).translate(FA_REPL)
    s = s.replace("کاال", "کالا")
    return s


OUT.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)
CUSTOMS_DIR.mkdir(parents=True, exist_ok=True)


BROKEN_QS = {2, 5, 7, 8, 12, 34, 51, 53, 62, 192, 202, 257, 258, 259, 260, 261}


def load_questions(selected):
    data = json.loads(FIN.read_text(encoding="utf-8"))
    nums = sorted(int(k) for k in data)
    chosen = []
    for n in nums:
        if n in BROKEN_QS:
            continue
        q = data[str(n)]
        if q.get("correct") is None:
            continue
        opts = q.get("options") or []
        if len([o for o in opts if o.strip()]) < 4:
            continue
        if selected is not None and n not in selected:
            continue
        chosen.append(q)
    return chosen


def build_segments(q):
    segs = {}
    segs["q"] = fa_clean(f"سوال {q['num']}. {q['question']}")
    for i in range(4):
        segs[f"o{i+1}"] = fa_clean(f"گزینه {NUMWORDS[i + 1]}. {q['options'][i]}")
    segs["a"] = fa_clean(f"پاسخ صحیح، گزینه {NUMWORDS[q['correct']]} است. {q['options'][q['correct'] - 1]}")
    segs["r"] = fa_clean((q.get("reason") or "").strip()) or "طبق مفاد قانون امور گمرکی"
    return segs


async def gen(text, out, rate):
    import edge_tts
    for attempt in range(8):
        try:
            c = edge_tts.Communicate(text, VOICE, rate=rate)
            await c.save(str(out))
            return
        except Exception as e:
            print(f"  retry {attempt} err {e}", flush=True)
            await asyncio.sleep(5)
    raise RuntimeError(f"TTS failed: {out}")


def mp3_dur(path):
    V1_L3 = {1:32,2:40,3:48,4:56,5:64,6:80,7:96,8:112,9:128,10:160,11:192,12:224,13:256,14:320}
    V2_L3 = {1:8,2:16,3:24,4:32,5:40,6:48,7:56,8:64,9:80,10:96,11:112,12:128,13:144,14:160}
    SRS = {3: [44100, 22050, 11025], 2: [22050, 11025, 8000], 0: [11025, 5500, 4000]}
    try:
        data = open(path, "rb").read()
    except Exception:
        return None
    n = len(data)
    i = 0
    if n >= 10 and data[:3] == b"ID3":
        i = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    while i + 4 <= n:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            ver = (data[i + 1] >> 3) & 0x03
            idx = (data[i + 2] >> 4) & 0x0F
            sidx = (data[i + 2] >> 2) & 0x03
            if idx in (0, 15) or sidx == 3 or ver == 1:
                i += 1
                continue
            break
        i += 1
    if i + 4 > n:
        return None
    ver = (data[i + 1] >> 3) & 0x03
    idx = (data[i + 2] >> 4) & 0x0F
    sidx = (data[i + 2] >> 2) & 0x03
    kbps = (V1_L3 if ver == 3 else V2_L3)[idx]
    audio_bytes = n - i
    return round(audio_bytes * 8 / (kbps * 1000.0), 2)


def render(q, out_file):
    props_file = WORK / f"props_q{q['num']}.json"
    props_file.write_bytes(json.dumps({"q": q}, ensure_ascii=False, indent=1).encode("utf-8"))
    cmd = [REMOTION_BIN, "render", str(INDEX_TS), "CustomsQuestion", str(out_file),
           f"--props={props_file}"]
    if CHROME:
        cmd += ["--browser-executable", CHROME]
    log = WORK / f"render_q{q['num']}.log"
    for attempt in range(3):
        try:
            with open(log, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(" ".join(str(c) for c in cmd) + "\n")
                rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=2400,
                                    cwd=str(REMOTION)).returncode
            if rc == 0 and out_file.exists() and out_file.stat().st_size > 0:
                return True
        except Exception as e:
            import traceback
            log.write_text(repr(e) + "\n" + traceback.format_exc(), encoding="utf-8", errors="replace")
        time.sleep(15)
    return False


FA_D = "۰۱۲۳۴۵۶۷۸۹"
FA_N = ["۱", "۲", "۳", "۴"]


def tofa(n):
    return "".join(FA_D[int(c)] for c in str(n))


def build_caption(q, limit=1000):
    header = ["سوال " + tofa(q["num"]),
              "📘 آزمون کارگزاری گمرک",
              "⚖️ قانون امور گمرکی ۱۴۰۴"]
    body = f"❓ {fa_clean(q['question'])}"
    opts = []
    for i, opt in enumerate(q["options"]):
        opts.append(f"{FA_N[i]}) {fa_clean(opt)}")
    ans = f"✅ پاسخ صحیح: گزینه {FA_N[q['correct'] - 1]}"
    if q.get("reason"):
        ans += f"\n📖 توضیح: {fa_clean(q['reason'])}"
    footer = ["🔹 @gomro68k_bot"]

    def make(with_opts, qmax=99999, rmax=99999):
        lines = list(header) + [""] + [body[:qmax].rstrip()] + [""]
        if with_opts:
            lines += opts
        lines += [""] + [ans[:rmax].rstrip()] + footer
        return "\n".join(lines)

    c = make(True)
    if len(c) <= limit:
        return c
    for cut in (700, 400, 200, 100):
        c = make(True, qmax=cut)
        if len(c) <= limit:
            return c
    c = make(False)
    if len(c) <= limit:
        return c
    for rmax in (500, 300, 150, 0):
        c = make(False, rmax=rmax)
        if len(c) <= limit:
            return c
    return make(False, qmax=200, rmax=0)[:limit]


def send_telegram(q, vpath):
    if not TOKEN:
        raise RuntimeError("GOMROK_TOKEN not set")
    import urllib.request
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    caption = build_caption(q)
    body = b""
    for name, val in [("chat_id", CHAT.encode()), ("caption", caption.encode("utf-8"))]:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += val + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="video"; filename="vid.mp4"\r\n'
    body += b"Content-Type: video/mp4\r\n\r\n"
    body += Path(vpath).read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(API + "/sendVideo", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode("utf-8", "replace"), caption
        except Exception as e:
            print("  send attempt", attempt, "err", e, flush=True)
            time.sleep(4)
    return "FAILED", caption


def build_batch():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", type=str)
    ap.add_argument("--list", type=str)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sel = None
    if args.range:
        a, b = map(int, args.range.split("-"))
        sel = set(range(a, b + 1))
    elif args.list:
        sel = set(map(int, args.list.split(",")))

    questions = load_questions(sel)
    print(f"selected {len(questions)} questions", flush=True)

    for q in questions:
        n = q["num"]
        data_file = OUT / f"q{n}.json"
        mp4 = OUT / f"q{n}.mp4"
        segs = build_segments(q)

        if not args.dry_run:
            print(f"--- TTS q{n} ---", flush=True)
            durs = {}
            for key in SEG_ORDER:
                out = CUSTOMS_DIR / f"q{n}_{key}.mp3"
                if not out.exists() or args.send:
                    asyncio.run(gen(segs[key], out, RATE))
                durs[key] = mp3_dur(out) or 1.0
            q["audio"] = durs
        else:
            q["audio"] = {k: 6.0 for k in SEG_ORDER}

        data_file.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {data_file.name} audio={q['audio']}", flush=True)

        if args.dry_run:
            continue

        if mp4.exists():
            mp4.unlink()
        print(f"--- render q{n} ---", flush=True)
        ok = render(q, mp4)
        if not ok:
            print(f"RENDER FAILED q{n}", flush=True)
            continue
        print(f"rendered {mp4.name} {mp4.stat().st_size:,} B", flush=True)

        if args.send:
            print(f"--- send q{n} ---", flush=True)
            res, caption = send_telegram(q, mp4)
            ok = res != "FAILED" and '"ok":true' in res
            print(f"send {'OK' if ok else 'FAILED'} -> {res[:200]}", flush=True)


if __name__ == "__main__":
    build_batch()