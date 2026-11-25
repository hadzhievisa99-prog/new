"""Sound design: SFX only (no music), mixed under the untouched voice.

Rules (from the user):
* one sound per visual idea; nearby events (<0.15 s) collapse to the most important one;
* the same sample never plays twice in a row; light pitch (+-1.2 semitones) and volume jitter;
* voice is never processed; SFX sit a little below the voice; a limiter acts on SFX only.
"""
import json
import os
import random
import subprocess

import numpy as np

from common import SOUNDS_DIR

SR = 48000
ROLES = ["opening", "title", "small", "card", "trans", "hit", "shine", "key", "riser", "snap", "down"]
PRIORITY = {r: i for i, r in enumerate(["opening", "hit", "riser", "trans", "card", "title", "shine",
                                        "snap", "down", "key", "small"])}
ROLE_GAIN_DB = {"opening": 1, "hit": 0, "riser": -2, "trans": -2, "title": -3, "card": -3, "shine": -3,
                "snap": -4, "down": -3, "key": -6, "small": -6}
GLITCH_FILES = ["trans_07", "trans_08"]
TRANS_ROLE = {"glitch": ("trans", GLITCH_FILES), "flash": ("hit", None), "shake": ("hit", None),
              "luma": ("shine", None)}


def library():
    meta = json.load(open(os.path.join(SOUNDS_DIR, "sounds.json"), encoding="utf-8"))
    lib = {r: sorted(k for k, v in meta.items() if v["role"] == r) for r in ROLES}
    return lib, meta


def load(name):
    p = os.path.join(SOUNDS_DIR, name + ".ogg")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def element_role(el):
    """Auto role for an element's IN moment (one sound per visual idea)."""
    if el.get("hook"):
        return "opening"
    if el["type"] == "card" or el.get("glass"):
        return "card"
    if el["type"] == "counter":
        return "riser" if el.get("count_dur", 1.2) >= 1.0 else "small"
    mode = (el.get("anim") or {}).get("mode", "word")
    if mode == "word_bounce" or el.get("bounce"):
        return "snap"
    if mode == "letter":
        return "key"
    if el.get("glow"):
        return "shine"
    return "title" if float(el.get("size", 84)) >= 80 else "small"


def collect_events(plan, elements):
    ev = []

    def add(t, spec, prio_role=None, gain=0.0, align="start"):
        if spec in (None, False, "none"):
            return
        if isinstance(spec, str) and spec in ROLES:
            ev.append({"t": t, "role": spec, "file": None, "gain": gain, "align": align})
        elif isinstance(spec, str):
            ev.append({"t": t, "role": spec.split("_")[0], "file": spec, "gain": gain, "align": align})

    for el in elements:
        s = el.get("sfx", "auto")
        if (el.get("_from_subs") or el["type"] == "bg") and s == "auto":
            continue
        t_in = el["start"] + (0.0 if el.get("hook") else 0.02)
        if isinstance(s, dict):
            add(t_in, s.get("in"), gain=s.get("gain", 0))
            if s.get("out"):
                add(el["end"] - 0.25, s["out"], gain=s.get("gain", 0))
        elif s == "auto":
            add(t_in, element_role(el))
        else:
            add(t_in, s)
    for tr in plan.get("transitions", []):
        s = tr.get("sfx", "auto")
        T = float(tr["at"])
        if s == "auto":
            role, files = TRANS_ROLE.get(tr.get("type", "whip"), ("trans", None))
            ev.append({"t": T - (0.12 if role == "trans" else 0.0), "role": role,
                       "file": random.Random(int(T * 100)).choice(files) if files else None, "gain": 0,
                       "align": "start", "exclude": GLITCH_FILES if role == "trans" else []})
        else:
            add(T - 0.12, s)
        if tr.get("riser"):
            ev.append({"t": T, "role": "riser", "file": None, "gain": -2, "align": "end", "force": True})
    for z in plan.get("zooms", []):
        s = z.get("sfx", "auto")
        if s == "auto":
            if z.get("ease") == "punch":
                ev.append({"t": z["start"], "role": "hit", "file": None, "gain": -3, "align": "start"})
        else:
            add(z["start"], s)
        if z.get("riser"):
            ev.append({"t": z["start"], "role": "riser", "file": None, "gain": -2, "align": "end", "force": True})
    for s in plan.get("sfx", []):
        ev.append({"t": float(s["t"]), "role": s.get("role") or s.get("file", "small").split("_")[0],
                   "file": s.get("file"), "gain": float(s.get("gain", 0)), "align": s.get("align", "start"),
                   "force": True})
    # one sound per visual idea: collapse events closer than 0.15 s (keep most important)
    ev.sort(key=lambda e: e["t"])
    out = []
    for e in ev:
        prev = [o for o in out if not o.get("force")]
        if prev and abs(e["t"] - prev[-1]["t"]) < 0.15 and not e.get("force"):
            if PRIORITY.get(e["role"], 99) < PRIORITY.get(prev[-1]["role"], 99):
                out[out.index(prev[-1])] = e
            continue
        out.append(e)
    return out


def choose_files(events, seed=7):
    lib, _ = library()
    rng = random.Random(seed)
    used = {}
    last = None
    for e in events:
        if e.get("file") and e["file"] != last:
            last = e["file"]
            continue
        cands = [f for f in lib.get(e["role"], []) if f != last and f not in e.get("exclude", [])] or lib["small"]
        m = min(used.get(f, 0) for f in cands)
        pool = [f for f in cands if used.get(f, 0) == m]
        f = rng.choice(pool)
        e["file"] = f
        used[f] = used.get(f, 0) + 1
        last = f
    return events


def pitch_shift(x, semis):
    r = 2 ** (semis / 12)
    n = int(len(x) / r)
    if n < 2:
        return x
    idx = np.arange(n) * r
    return np.stack([np.interp(idx, np.arange(len(x)), x[:, c]) for c in range(2)], 1).astype(np.float32)


def load_voice(path, timeline):
    if not path or not os.path.exists(path):
        return np.zeros((int(timeline.dur * SR), 2), np.float32)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    v = np.frombuffer(raw, np.float32).reshape(-1, 2)
    parts = []
    xf = int(0.008 * SR)
    for a, b in timeline.keep:
        seg = v[int(a * SR):int(b * SR)].copy()
        if len(seg) > 2 * xf and len(timeline.keep) > 1:     # micro fades at jump cuts (no clicks)
            seg[:xf] *= np.linspace(0, 1, xf)[:, None]
            seg[-xf:] *= np.linspace(1, 0, xf)[:, None]
        parts.append(seg)
    out = np.concatenate(parts) if parts else np.zeros((0, 2), np.float32)
    n = int(round(timeline.dur * SR))
    if len(out) < n:
        out = np.pad(out, ((0, n - len(out)), (0, 0)))
    return out[:n]


def mix(plan, elements, timeline, voice_path, out_wav, sfx_only_wav=None):
    a = plan.get("audio", {})
    events = choose_files(collect_events(plan, elements), int(a.get("seed", 7)))
    voice = load_voice(voice_path, timeline)
    n = len(voice)
    # voice level = RMS of active speech
    mono = np.abs(voice).mean(1)
    hop = int(0.05 * SR)
    if n > hop:
        fr = np.sqrt(np.mean(mono[: n // hop * hop].reshape(-1, hop) ** 2, 1) + 1e-12)
        act = fr[fr > np.percentile(fr, 60) * 0.5]
        v_db = 20 * np.log10(np.median(act) + 1e-9) if len(act) else -20
        if v_db < -45:
            v_db = -20.0
    else:
        v_db = -20.0
    # samples are normalised to -14 dBFS (loudest 300 ms); target = voice level + sfx_gain_db
    base_db = (v_db + float(a.get("sfx_gain_db", -3))) - (-14.0)
    rng = random.Random(int(a.get("seed", 7)) + 1)
    pj, vj = float(a.get("pitch_jitter", 1.2)), float(a.get("vol_jitter_db", 1.5))
    sfx = np.zeros((n + SR * 5, 2), np.float32)
    log = []
    cache = {}
    for e in events:
        if e["file"] not in cache:
            cache[e["file"]] = load(e["file"])
        x = pitch_shift(cache[e["file"]], rng.uniform(-pj, pj))
        g_db = base_db + ROLE_GAIN_DB.get(e["role"], -6) + e.get("gain", 0) + rng.uniform(-vj, vj)
        x = x * 10 ** (g_db / 20)
        t0 = e["t"] - (len(x) / SR if e.get("align") == "end" else 0)
        i0 = int(max(0, t0) * SR)
        if t0 < 0:
            x = x[int(-t0 * SR):]
        sfx[i0:i0 + len(x)] += x[: len(sfx) - i0]
        log.append({"t": round(e["t"], 3), "role": e["role"], "file": e["file"], "db": round(float(g_db), 1)})
    sfx = sfx[:n]
    # limiter on SFX only: keep voice+sfx under -1 dBFS, voice untouched
    ceiling = 10 ** (-1 / 20)
    head = np.clip(ceiling - np.abs(voice), 0.0, None)
    need = np.abs(sfx)
    g = np.ones(n, np.float32)
    over = need.max(1) > head.min(1)
    if over.any():
        g[over] = (head.min(1)[over] / (need.max(1)[over] + 1e-9))
        k = int(0.01 * SR)
        g = np.lib.stride_tricks.sliding_window_view(np.pad(g, (k, k), constant_values=1), 2 * k + 1).min(1)[:n]
    sfx *= g[:, None]
    total = voice + sfx
    write_wav(out_wav, total)
    if sfx_only_wav:
        write_wav(sfx_only_wav, sfx)
    return log


def write_wav(path, x):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "pcm_s16le", path], input=np.clip(x, -1, 1).astype(np.float32).tobytes(), check=True)
