#!/usr/bin/env python3
"""Step 2: speech -> words with timings (work/words.json).

  python3 transcribe.py --work work [--lang ru] [--model small] [--budget 240]
  python3 transcribe.py --work work --text script.txt      # fallback: align a known text
  python3 transcribe.py --work work --fix fixed.txt        # correct Whisper's words, keep its timings

Uses faster-whisper offline (CPU, int8) with word timestamps. Audio is processed in
45-second windows; finished windows are cached, so a re-run continues where it stopped.
If Whisper / its model is unavailable, --text aligns the given text to detected speech
regions (syllable-proportional) so captions are still roughly in sync.
"""
import argparse
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Budget, load_json, save_json, work_paths  # noqa: E402

WIN = 45.0
SR = 16000


def load_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def speech_regions(x, min_gap=0.25):
    hop = int(0.02 * SR)
    e = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, "same")[::hop] + 1e-12)
    db = 20 * np.log10(e)
    thr = max(np.percentile(db, 95) - 30, np.percentile(db, 20) + 6)
    on = db > thr
    regs, s = [], None
    for i, v in enumerate(on):
        t = i * hop / SR
        if v and s is None:
            s = t
        if not v and s is not None:
            regs.append([s, t])
            s = None
    if s is not None:
        regs.append([s, len(x) / SR])
    merged = []
    for r in regs:
        if merged and r[0] - merged[-1][1] < min_gap:
            merged[-1][1] = r[1]
        elif r[1] - r[0] > 0.08:
            merged.append(r)
    return merged


def align_text(text, regs):
    words = text.split()
    syl = [max(1, len(re.findall(r"[аеёиоуыэюяaeiouy]", w.lower()))) for w in words]
    total_t = sum(r[1] - r[0] for r in regs) or 1.0
    total_s = sum(syl)
    out, ri, cur = [], 0, regs[0][0] if regs else 0.0
    for w, s in zip(words, syl):
        d = total_t * s / total_s
        if ri < len(regs) and cur + d > regs[ri][1] + 0.05 and ri + 1 < len(regs):
            ri += 1
            cur = regs[ri][0]
        out.append({"w": w, "s": round(cur, 3), "e": round(cur + d, 3)})
        cur += d
    return out


def fix_words(words, text):
    """Replace recognised words by the corrected text, keeping timings (difflib alignment)."""
    import difflib
    new = text.split()
    norm = lambda w: re.sub(r"[^\wё]+", "", w.lower())
    sm = difflib.SequenceMatcher(a=[norm(w["w"]) for w in words], b=[norm(w) for w in new], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out += [{**words[i1 + k], "w": new[j1 + k]} for k in range(i2 - i1)]
        elif j2 > j1:
            if i2 > i1:
                s0, e0 = words[i1]["s"], words[i2 - 1]["e"]
            else:  # insertion: squeeze between neighbours
                s0 = words[i1 - 1]["e"] if i1 > 0 else 0.0
                e0 = words[i1]["s"] if i1 < len(words) else s0 + 0.3 * (j2 - j1)
            lens = [max(1, len(w)) for w in new[j1:j2]]
            tot, cur = sum(lens), s0
            for w, L in zip(new[j1:j2], lens):
                d = (e0 - s0) * L / tot
                out.append({"w": w, "s": round(cur, 3), "e": round(cur + d, 3)})
                cur += d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--model", default="small", help="tiny/base/small/medium (small = good ru quality on CPU)")
    ap.add_argument("--text", help="fallback: plain text file of what is said")
    ap.add_argument("--fix", help="text file with the corrected transcript (same speech, fixed words)")
    ap.add_argument("--budget", type=float, default=240)
    a = ap.parse_args()
    if a.fix:
        P = work_paths(a.work)
        wj = load_json(P["words"])
        with open(a.fix, encoding="utf-8") as f:
            wj["words"] = fix_words(wj["words"], f.read())
        wj["fixed"] = True
        save_json(P["words"], wj)
        print("DONE fixed:", " ".join(w["w"] for w in wj["words"]))
        return
    P = work_paths(a.work)
    B = Budget(a.budget)
    x = load_audio(P["voice"])
    dur = len(x) / SR

    if a.text:
        with open(a.text, encoding="utf-8") as f:
            words = align_text(f.read(), speech_regions(x))
        save_json(P["words"], {"source": "aligned-text", "words": words})
        print(f"DONE aligned {len(words)} words (approximate timings)")
        return

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], check=False)
        from faster_whisper import WhisperModel
    try:
        model = WhisperModel(a.model, device="cpu", compute_type="int8")
    except Exception as e:  # no network / no model
        print(f"WHISPER UNAVAILABLE: {e}\nUse --text script.txt (ask the user for the spoken text).")
        sys.exit(2)

    cache_dir = os.path.join(a.work, "asr_parts")
    os.makedirs(cache_dir, exist_ok=True)
    n = max(1, int(np.ceil(dur / WIN)))
    for i in range(n):
        cp = os.path.join(cache_dir, f"w_{i:03d}.json")
        if os.path.exists(cp):
            continue
        if not B.ok(WIN * 1.2) and i > 0:
            print(f"BUDGET: {i}/{n} windows done. Re-run the same command to continue.")
            return
        # 1 s overlap on each side, words are kept only inside the window
        t0, t1 = max(0, i * WIN - 1.0), min(dur, (i + 1) * WIN + 1.0)
        seg_audio = x[int(t0 * SR):int(t1 * SR)]
        segs, _ = model.transcribe(seg_audio, language=a.lang or None, word_timestamps=True,
                                   vad_filter=True, beam_size=5, condition_on_previous_text=False)
        ws = []
        for s in segs:
            for w in (s.words or []):
                ws_ = w.start + t0
                if i * WIN <= ws_ < (i + 1) * WIN:
                    ws.append({"w": w.word.strip(), "s": round(ws_, 3), "e": round(w.end + t0, 3),
                               "p": round(float(w.probability), 3)})
        save_json(cp, ws)
        print(f"window {i + 1}/{n}: {len(ws)} words")
    words = []
    for i in range(n):
        words += load_json(os.path.join(cache_dir, f"w_{i:03d}.json"), [])
    words = [w for w in words if w["w"]]
    save_json(P["words"], {"source": f"faster-whisper-{a.model}", "lang": a.lang, "words": words})
    print("DONE", len(words), "words:", " ".join(w["w"] for w in words)[:400])


if __name__ == "__main__":
    main()
