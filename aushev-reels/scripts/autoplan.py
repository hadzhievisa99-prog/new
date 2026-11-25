#!/usr/bin/env python3
"""Draft a plan.json from words.json + meta.json + track.json.

  python3 autoplan.py --work work --out plan.json [--hook "3 ОШИБКИ"] [--trim-pauses]

The draft follows the house rules (hook in the first second, something new every <=2 s,
transitions on every cut, titles outside the face, everything inside the safe zone). It is a
STARTING POINT: Claude must then rewrite titles/accents/zooms by meaning and run --check.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SAFE, load_json, save_json, work_paths  # noqa: E402
from camera import Camera, Timeline  # noqa: E402

TRANS_CYCLE = [("whip", "left"), ("zoom_in", None), ("slide", "up"), ("glitch", None), ("whip", "right"),
               ("spin", "right"), ("zoom_out", None), ("blur", None)]
TITLE_MODES = [("word_bounce", "up"), ("word", "left"), ("letter", "up"), ("word", "right"), ("word_bounce", "down")]


def sentences(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"]) or (len(cur) >= 9):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    merged = []
    for sw in out:                      # "Первая." + "Слабый хук..." -> one idea
        if merged and len(merged[-1]) < 3:
            merged[-1] = merged[-1] + sw
        else:
            merged.append(sw)
    return merged


def pauses_to_keep(words, dur, min_pause=0.45, pad=0.12):
    keep, s = [], 0.0
    for a, b in zip(words, words[1:]):
        if b["s"] - a["e"] > min_pause:
            keep.append([s, a["e"] + pad])
            s = b["s"] - pad
    keep.append([s, dur])
    return [[round(a, 3), round(b, 3)] for a, b in keep if b - a > 0.2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="plan.json")
    ap.add_argument("--hook", help="hook title text (default: first words of the speech)")
    ap.add_argument("--trim-pauses", action="store_true", help="jump-cut pauses longer than 0.45 s")
    ap.add_argument("--keep", help='source ranges to keep, e.g. "0-3.3,3.7-9.15,9.6-14.9"')
    a = ap.parse_args()
    P = work_paths(a.work)
    meta = load_json(P["meta"])
    words_src = (load_json(P["words"], {}) or {}).get("words", [])
    track = load_json(P["track"])
    keep = pauses_to_keep(words_src, meta["dur"]) if a.trim_pauses and words_src else None
    if a.keep:
        keep = [[float(x) for x in r.split("-")] for r in a.keep.split(",")]
    tl = Timeline(keep, meta["dur"])
    words = []
    for w in words_src:
        s = tl.to_out(w["s"])
        if s is not None:
            words.append({"w": w["w"], "s": s, "e": tl.to_out(w["e"]) or s + 0.3})
    dur = tl.dur
    plan_stub = {"video": {"work": os.path.relpath(a.work, os.path.dirname(os.path.abspath(a.out))),
                           "eye_line": "auto", "keep": keep}}
    cam = Camera(plan_stub, meta, track, tl)
    # where is the face (worst case over the video)? choose the title zone above or below it
    if track:
        import numpy as np
        tops, bots = [], []
        for t in np.arange(0, dur, 0.2):
            i = min(len(track["eye_y"]) - 1, int(round(tl.to_src(t) * 30)))
            M = cam.base_matrix(t)
            y = M[1, 1] * track["eye_y"][i] + M[1, 2]
            ed = track["eye_d"][i] * M[1, 1]
            tops.append(y - 1.2 * ed)
            bots.append(y + 1.75 * ed)
        face_top, face_bot = float(min(tops)), float(max(bots))
    else:
        face_top, face_bot = cam.eye_y - 250, cam.eye_y + 380
    room_above = face_top - SAFE["y0"]
    room_below = SAFE["y1"] - face_bot
    if room_above >= 260:
        title_y, sub_y = SAFE["y0"] + 110, min(SAFE["y1"] - 120, max(face_bot + 80, 1330))
    else:
        title_y = int(min(max(face_bot + 110, 1100), SAFE["y1"] - 300))
        sub_y = int(SAFE["y1"] - 95)
    title_y = int(title_y)
    els, zooms, trans = [], [], []
    sents = sentences(words)
    first = []
    for w in (sents[0] if sents else [])[:3]:
        first.append(w["w"])
        if re.search(r"[,.!?:]$", w["w"]):
            break
    hook_text = a.hook or (" ".join(first).strip(" ,.!?:") or "СМОТРИ")
    hook_end = min(dur, max(2.4, sents[0][-1]["e"] + 0.2) if sents else 2.8)
    els.append({"id": "hook", "type": "text", "hook": True, "text": hook_text.upper(), "start": 0.0,
                "end": round(hook_end, 2), "x": 540, "y": title_y, "size": 120, "weight": "black",
                "max_width": 820, "anim": {"mode": "word_bounce", "dir": "up", "io": "both"},
                "bounce": {"io": "in"}, "glow": {"radius": 20, "exposure": 0.8}, "shadow": True})
    zooms.append({"start": 0.0, "end": 0.6, "scale": 1.12, "ease": "punch", "out": 0.4})
    if sents and len(sents[0]) > 3:     # second beat inside the hook: punch on the longest word
        mid = hook_end / 2
        kw = min([w for w in sents[0] if len(w["w"]) >= 4] or sents[0], key=lambda w: abs(w["s"] - mid))
        if kw["s"] > 1.0:
            zooms.append({"start": round(kw["s"], 2), "end": round(kw["s"] + 0.5, 2), "scale": 1.07,
                          "ease": "punch", "out": 0.3})
    # one title/card per sentence (after the hook), alternating styles
    body = sents[1:]
    for i, sw in enumerate(body):
        st, en = sw[0]["s"], min(dur, sw[-1]["e"] + 0.25)
        if i + 1 < len(body):
            en = min(en, body[i + 1][0]["s"] - 0.05)
        st = max(st, hook_end + 0.05) if i == 0 else st
        if en - st < 0.8:
            continue
        key = max(sw, key=lambda w: len(re.sub(r"\W", "", w["w"])))
        mode, d = TITLE_MODES[i % len(TITLE_MODES)]
        tw = [w["w"] for w in sw[:5]]
        while len(tw) > 2 and len(re.sub(r"\W", "", tw[-1])) <= 2:    # no dangling prepositions
            tw.pop()
        txt = " ".join(tw).strip(" ,.")
        el = {"id": f"title{i + 1}", "type": "card" if i % 2 == 0 else "text", "text": txt,
              "start": round(st, 2), "end": round(en, 2), "x": 540, "y": title_y, "size": 66 if i % 2 == 0 else 84,
              "max_width": 760, "accent": [key["w"]], "anim": {"mode": mode, "dir": d, "io": "both"}, "shadow": True}
        if i % 3 == 2:
            el["glow"] = True
        if i % 2 == 1:
            el["bounce"] = {"io": "in"}
        els.append(el)
        zooms.append({"start": round(key["s"], 2), "end": round(min(en, key["e"] + 0.6), 2),
                      "scale": 1.08 if i % 2 else 1.1, "ease": "punch" if i % 2 else "smooth", "out": 0.3})
    if words:
        els.append({"id": "subs", "type": "subs", "start": round(min(hook_end, words[-1]["e"]), 2), "end": round(dur, 2),
                    "y": sub_y, "size": 60, "max_width": 640, "max_words": 3, "karaoke": True,
                    "modes": ["word", "word_bounce", "letter"], "dirs": ["up", "left", "right", "down"],
                    "shadow": True})
    for i, c in enumerate(tl.cuts(meta.get("cuts"))):
        t, d = TRANS_CYCLE[i % len(TRANS_CYCLE)]
        tr = {"at": round(c, 3), "type": t, "dur": 0.34}
        if d:
            tr["dir"] = d
        trans.append(tr)
    plan = {"video": {**plan_stub["video"], "grade": {"contrast": 1.06, "saturation": 1.06, "vignette": 0.16}},
            "output": "out/reel.mp4", "elements": els, "zooms": zooms, "transitions": trans, "sfx": [],
            "audio": {"sfx_gain_db": -3, "pitch_jitter": 1.2, "vol_jitter_db": 1.5, "seed": 7}}
    if not keep:
        plan["video"].pop("keep")
    save_json(a.out, plan)
    from render import fit_safe
    fit_safe(a.out)
    print(f"DRAFT {a.out}: {len(els)} elements, {len(zooms)} zooms, {len(trans)} transitions, "
          f"title_y={title_y} subs_y={sub_y} (face {face_top:.0f}-{face_bot:.0f}px). "
          f"Now edit by meaning and run render.py --check")


if __name__ == "__main__":
    main()
