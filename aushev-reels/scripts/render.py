#!/usr/bin/env python3
"""Check, preview and render a plan.json.

  python3 render.py plan.json --check
  python3 render.py plan.json --preview 0.4,1.2,3.5 [--safe]
  python3 render.py plan.json --render [--budget 240]     # re-run until it prints DONE

Rendering happens in 2-second chunks (work/chunks). A chunk is only re-rendered when something
that touches its time range changed, so editing one title re-renders ~1 chunk.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FPS, H, SAFE, W, Budget, load_json, save_json, work_paths, SOUNDS_DIR  # noqa: E402
from camera import Camera, Timeline  # noqa: E402
from text import TextElement  # noqa: E402
import audio  # noqa: E402

CHUNK = 2.0
def _code_version():
    h = hashlib.sha1(b"aushev-reels-1.0")
    d = os.path.dirname(os.path.abspath(__file__))
    for f in ("common.py", "text.py", "fx.py", "camera.py", "render.py"):
        with open(os.path.join(d, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


VERSION = _code_version()
MODES = {"word", "letter", "word_bounce"}
DIRS = {"up", "down", "left", "right"}
IOS = {"in", "out", "both"}
TYPES = {"text", "card", "counter", "subs"}
TRANS = {"whip", "slide", "zoom_in", "zoom_out", "spin", "glitch", "flash", "blur", "shake", "luma"}


# ------------------------------------------------------------------ plan
def load_plan(path):
    plan = load_json(path)
    base = os.path.dirname(os.path.abspath(path))
    v = plan.setdefault("video", {})
    v["work"] = os.path.normpath(os.path.join(base, v.get("work", "work")))
    out = plan.get("output") or os.path.join(v["work"], "final.mp4")
    plan["output"] = os.path.normpath(os.path.join(base, out))
    return plan


def split_groups(words, max_words=3, max_chars=22, gap=0.35):
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars
                    or w["s"] - cur[-1]["e"] > gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def expand(plan, timeline, words_src):
    """Expand 'subs' into timed text elements (output timeline)."""
    els = []
    words = []
    for w in words_src:
        s, e = timeline.to_out(w["s"]), timeline.to_out(w["e"])
        if s is not None:
            words.append({"w": w["w"], "s": s, "e": e if e is not None else s + 0.25})
    for i, el in enumerate(plan.get("elements", [])):
        el = dict(el)
        el.setdefault("id", f"el{i}")
        if el.get("type") != "subs":
            el.setdefault("type", "text")
            els.append(el)
            continue
        ws = [w for w in words if el.get("start", 0) <= w["s"] < el.get("end", 1e9)]
        groups = split_groups(ws, el.get("max_words", 3), el.get("max_chars", 22), el.get("gap", 0.35))
        modes = el.get("modes") or [(el.get("anim") or {}).get("mode", "word")]
        dirs = el.get("dirs") or [(el.get("anim") or {}).get("dir", "up")]
        for gi, g in enumerate(groups):
            nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
            end = g[-1]["e"] + 0.35
            if nxt is not None and nxt - g[-1]["e"] < 0.6:
                end = nxt - 0.02
            end = min(end, el.get("end", 1e9))
            sub = {k: v for k, v in el.items() if k not in ("type", "start", "end", "modes", "dirs", "max_words",
                                                             "max_chars", "gap", "id")}
            anim = dict(el.get("anim") or {})
            anim["mode"] = modes[gi % len(modes)]
            anim["dir"] = dirs[gi % len(dirs)]
            anim.setdefault("io", "both")
            if el.get("sync", True):
                anim["times"] = [w["s"] - 0.04 for w in g]
            sub.update({"type": "text", "id": f"{el['id']}_{gi}", "text": " ".join(w["w"] for w in g),
                        "start": max(el.get("start", 0), g[0]["s"] - 0.06), "end": max(end, g[0]["s"] + 0.3),
                        "anim": anim, "_from_subs": True})
            sub.setdefault("size", 66)
            sub.setdefault("y", 1290)
            els.append(sub)
    for el in els:
        if el["type"] == "card":
            el.setdefault("glass", True)
    return sorted(els, key=lambda e: (e.get("z", 0), e["start"]))


class Project:
    def __init__(self, plan_path):
        self.plan_path = plan_path
        self.plan = load_plan(plan_path)
        self.P = work_paths(self.plan["video"]["work"])
        self.meta = load_json(self.P["meta"])
        if not self.meta:
            sys.exit(f"no {self.P['meta']} — run prepare.py first")
        self.track = load_json(self.P["track"])
        wj = load_json(self.P["words"], {}) or {}
        self.timeline = Timeline(self.plan["video"].get("keep"), self.meta["dur"])
        self.elements = expand(self.plan, self.timeline, wj.get("words", []))
        self.dur = float(self.plan["video"].get("duration") or self.timeline.dur)
        self.cam = Camera(self.plan, self.meta, self.track, self.timeline)
        self.renderers = [TextElement(e) for e in self.elements]
        self.n_frames = int(round(self.dur * FPS))

    # ---------------------------------------------------------------- checks
    def check(self):
        errs, warns = [], []
        S = SAFE
        for el, r in zip(self.elements, self.renderers):
            tag = f"[{el['id']}]"
            if el["type"] not in TYPES:
                errs.append(f"{tag} unknown type {el['type']}")
            a = el.get("anim") or {}
            if a.get("mode", "word") not in MODES:
                errs.append(f"{tag} anim.mode must be one of {sorted(MODES)}")
            if a.get("dir", "up") not in DIRS:
                errs.append(f"{tag} anim.dir must be one of {sorted(DIRS)}")
            if a.get("io", "both") not in IOS:
                errs.append(f"{tag} anim.io must be in/out/both")
            if not (0 <= el["start"] < el["end"] <= self.dur + 1e-3):
                errs.append(f"{tag} bad time range {el['start']}..{el['end']} (video {self.dur:.2f}s)")
            box = r.extents()
            if box is None:
                continue
            x0, y0, x1, y1 = box
            bad = []
            if x0 < S["x0"]:
                bad.append(f"left {x0:.0f}<{S['x0']}")
            if x1 > S["x1"]:
                bad.append(f"right {x1:.0f}>{S['x1']}")
            if y0 < S["y0"]:
                bad.append(f"top {y0:.0f}<{S['y0']}")
            if y1 > S["y1"]:
                bad.append(f"bottom {y1:.0f}>{S['y1']}")
            if y1 > S["y_right_cut"] and x1 > S["x1_low"]:
                bad.append(f"right {x1:.0f}>{S['x1_low']} below y={S['y_right_cut']}")
            if bad:
                errs.append(f"{tag} '{el.get('text', '')[:30]}' outside safe zone (incl. animation): "
                            + ", ".join(bad) + f" box=({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})")
        # text must not sit on the speaker's face
        if self.track and not self.plan["video"].get("allow_face_overlap"):
            for el, r in zip(self.elements, self.renderers):
                box = r.extents()
                if box is None:
                    continue
                tm = (el["start"] + el["end"]) / 2
                fb = self.face_box(tm)
                ix = max(0, min(box[2], fb[2]) - max(box[0], fb[0]))
                iy = max(0, min(box[3], fb[3]) - max(box[1], fb[1]))
                area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
                if ix * iy / area > 0.15:
                    warns.append(f"[{el['id']}] covers the speaker's face ({ix * iy / area * 100:.0f}% of the text box; "
                                 f"face y {fb[1]:.0f}-{fb[3]:.0f}) — move it above/below the face")
        for tr in self.plan.get("transitions", []):
            if tr.get("type", "whip") not in TRANS:
                errs.append(f"[transition@{tr.get('at')}] type must be one of {sorted(TRANS)}")
        for s in self.plan.get("sfx", []):
            if s.get("file") and not os.path.exists(os.path.join(SOUNDS_DIR, s["file"] + ".ogg")):
                errs.append(f"[sfx@{s.get('t')}] no such sound {s['file']}")
            if s.get("role") and s["role"] not in audio.ROLES:
                errs.append(f"[sfx@{s.get('t')}] role must be one of {audio.ROLES}")
        # rhythm: something must happen at least every ~2 s
        ev = sorted({round(e["start"], 2) for e in self.elements} |
                    {round(e["end"], 2) for e in self.elements if (e.get("anim") or {}).get("io", "both") != "in"} |
                    {round(float(t["at"]), 2) for t in self.plan.get("transitions", [])} |
                    {round(float(z["start"]), 2) for z in self.plan.get("zooms", [])})
        pts = [0.0] + [e for e in ev if 0 <= e <= self.dur] + [self.dur]
        for a, b in zip(pts, pts[1:]):
            if b - a > 2.2:
                warns.append(f"nothing happens {a:.1f}-{b:.1f}s ({b - a:.1f}s) — add an animation/zoom/transition")
        if not any(e["start"] < 0.6 for e in self.elements):
            warns.append("no animated hook in the first 0.6 s")
        cuts = self.timeline.cuts(self.meta.get("cuts"))
        tr_at = [float(t["at"]) for t in self.plan.get("transitions", [])]
        for c in cuts:
            if not any(abs(c - x) < 0.1 for x in tr_at):
                warns.append(f"cut at {c:.2f}s has no transition")
        if not self.cam.eye_line_ok:
            warns.append("eyes cannot be kept on one line everywhere without a bigger reframe "
                         "(raise video.max_reframe or accept small drift)")
        if self.track and self.track.get("detect_rate", 1) < 0.5:
            warns.append(f"face detected only on {self.track['detect_rate'] * 100:.0f}% of frames")
        if not self.track:
            warns.append("no track.json — run track.py (eye line uses a default position)")
        if self.plan.get("music"):
            errs.append("plan has 'music' — background music is not allowed (SFX only)")
        return errs, warns

    def face_box(self, t):
        """Approximate face rectangle in output px at time t (from eye position and eye distance)."""
        ts = self.timeline.to_src(t)
        i = int(min(max(round(ts * FPS), 0), self.meta["frames"] - 1))
        ex, ey = self.track["eye_x"][i], self.track["eye_y"][i]
        ed = self.track["eye_d"][i]
        M = self.cam.base_matrix(t)
        s = M[0, 0]
        x, y = M[0, 0] * ex + M[0, 2], M[1, 1] * ey + M[1, 2]
        return x - 1.25 * ed * s, y - 1.2 * ed * s, x + 1.25 * ed * s, y + 1.75 * ed * s

    # ---------------------------------------------------------------- frames
    def frame(self, t, reader):
        img = self.cam.render_frame(reader.get, t)
        img = np.ascontiguousarray(img, dtype=np.float32)
        for r in self.renderers:
            r.draw(img, t)
        return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)

    def chunk_key(self, i):
        t0, t1 = i * CHUNK - 1.0, (i + 1) * CHUNK + 1.0
        items = [e for e in self.elements if e["start"] < t1 and e["end"] > t0]
        zs = [z for z in self.plan.get("zooms", []) if z["start"] < t1 and z["end"] + 1.5 > t0]
        trs = [x for x in self.plan.get("transitions", []) if t0 < float(x["at"]) < t1]
        v = {k: val for k, val in self.plan["video"].items() if k != "work"}
        blob = json.dumps([VERSION, v, items, zs, trs, self.track is not None and self.track.get("frames"),
                           os.path.getmtime(self.P["track"]) if os.path.exists(self.P["track"]) else 0],
                          sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


class Reader:
    """Sequential frame reader over the prepared source with cheap seeking."""

    def __init__(self, path, pw, ph, n):
        self.path, self.pw, self.ph, self.n = path, pw, ph, n
        self.proc, self.cur, self.last = None, -10, None

    def _open(self, idx):
        if self.proc:
            self.proc.kill()
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{idx / FPS:.4f}", "-i", self.path,
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=self.pw * self.ph * 3)
        self.cur = idx - 1

    def get(self, ts):
        idx = int(min(max(round(ts * FPS), 0), self.n - 1))
        if idx == self.cur and self.last is not None:
            return self.last
        if self.proc is None or idx <= self.cur or idx - self.cur > 40:
            self._open(idx)
        fs = self.pw * self.ph * 3
        while self.cur < idx:
            buf = self.proc.stdout.read(fs)
            if len(buf) < fs:
                break
            self.cur += 1
            self.last = np.frombuffer(buf, np.uint8).reshape(self.ph, self.pw, 3)
        return self.last

    def close(self):
        if self.proc:
            self.proc.kill()


def fit_safe(plan_path):
    """Move/shrink elements until their animated extents are inside the Instagram safe zone.
    Writes the plan back (keeps a .bak). Subs templates are fitted on their widest possible group."""
    import shutil
    S = SAFE
    raw = load_json(plan_path)
    pr = Project(plan_path)
    changed = []
    for el in raw.get("elements", []):
        probe = dict(el)
        if el.get("type") == "subs":
            subs = [e for e in pr.elements if str(e["id"]).startswith(str(el.get("id", "")) + "_")]
            if not subs:
                continue
            probe = max(subs, key=lambda e: len(e.get("text", "")))
        probe.setdefault("type", "text")
        if probe["type"] == "card":
            probe.setdefault("glass", True)
        for _ in range(25):
            box = TextElement(probe).extents()
            if box is None:
                break
            x0, y0, x1, y1 = box
            dy = (S["y0"] - y0) if y0 < S["y0"] else (S["y1"] - y1) if y1 > S["y1"] else 0
            right = S["x1_low"] if y1 > S["y_right_cut"] else S["x1"]
            dx = 0
            if x1 > right or x0 < S["x0"]:
                dx = (S["x0"] + right) / 2 - (x0 + x1) / 2
            if dy == 0 and x1 + dx <= right + 0.5 and x0 + dx >= S["x0"] - 0.5:
                if dx:
                    probe["x"] = round(probe.get("x", 540) + dx)
                break
            if (x1 - x0) > right - S["x0"] or (y1 - y0) > S["y1"] - S["y0"]:
                probe["size"] = round(float(probe.get("size", 84)) * 0.94, 1)
            probe["x"] = round(probe.get("x", 540) + dx)
            probe["y"] = round(probe.get("y", 960) + dy)
        for k in ("x", "y", "size"):
            if k in probe and probe[k] != el.get(k, {"x": 540, "y": 960, "size": 84}[k]):
                el[k] = probe[k]
                changed.append(f"{el.get('id')}.{k}={probe[k]}")
    if changed:
        shutil.copy(plan_path, plan_path + ".bak")
        save_json(plan_path, raw)
    print("FIT-SAFE:", ", ".join(changed) if changed else "nothing to change")


# ------------------------------------------------------------------ commands
def cmd_check(pr, quiet=False):
    errs, warns = pr.check()
    for w in warns:
        print("WARN ", w)
    for e in errs:
        print("ERROR", e)
    if not quiet:
        ev = audio.collect_events(pr.plan, pr.elements)
        print(f"elements={len(pr.elements)} transitions={len(pr.plan.get('transitions', []))} "
              f"zooms={len(pr.plan.get('zooms', []))} sfx_events={len(ev)} duration={pr.dur:.2f}s "
              f"eye_line_y={pr.cam.eye_y:.0f}px scale={pr.cam.base:.3f}")
    print("CHECK OK" if not errs else f"CHECK FAILED ({len(errs)} errors)")
    return not errs


def draw_safe(img):
    import cv2
    S = SAFE
    c = (255, 60, 60)
    cv2.rectangle(img, (S["x0"], S["y0"]), (S["x1"], S["y_right_cut"]), c, 2)
    cv2.rectangle(img, (S["x0"], S["y_right_cut"]), (S["x1_low"], S["y1"]), c, 2)
    return img


def cmd_preview(pr, times, safe):
    from PIL import Image
    os.makedirs(pr.P["preview"], exist_ok=True)
    rd = Reader(pr.P["src"], pr.meta["pw"], pr.meta["ph"], pr.meta["frames"])
    tiles = []
    for t in times:
        t = min(max(0, t), pr.dur - 1 / FPS)
        img = pr.frame(t, rd)
        if safe:
            img = draw_safe(img.copy())
        p = os.path.join(pr.P["preview"], f"t{t:07.3f}.png")
        Image.fromarray(img).save(p)
        tiles.append(Image.fromarray(img).resize((270, 480)))
        print("preview", p)
    rd.close()
    if len(tiles) > 1:
        cols = min(6, len(tiles))
        rows = math.ceil(len(tiles) / cols)
        sheet = Image.new("RGB", (cols * 270, rows * 480), (20, 20, 20))
        for i, tl in enumerate(tiles):
            sheet.paste(tl, ((i % cols) * 270, (i // cols) * 480))
        sp = os.path.join(pr.P["preview"], "sheet.jpg")
        sheet.save(sp, quality=88)
        print("sheet", sp)


_WORKER = {}


def _render_chunk(plan_path, i, crf):
    """Worker: render one chunk (each process keeps its own Project/Reader)."""
    t_start = time.time()
    if _WORKER.get("plan") != plan_path:
        _WORKER["plan"] = plan_path
        _WORKER["pr"] = Project(plan_path)
        _WORKER["rd"] = None
    pr = _WORKER["pr"]
    if _WORKER["rd"] is None:
        _WORKER["rd"] = Reader(pr.P["src"], pr.meta["pw"], pr.meta["ph"], pr.meta["frames"])
    rd = _WORKER["rd"]
    f0 = int(i * CHUNK * FPS)
    f1 = min(pr.n_frames, int((i + 1) * CHUNK * FPS))
    out = os.path.join(pr.P["chunks"], f"c{i:04d}.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                            "-threads", "2", "-pix_fmt", "yuv420p", "-g", str(FPS), out + ".tmp.mp4"],
                           stdin=subprocess.PIPE)
    for f in range(f0, f1):
        enc.stdin.write(pr.frame(f / FPS, rd).tobytes())
    enc.stdin.close()
    enc.wait()
    os.replace(out + ".tmp.mp4", out)
    return i, pr.chunk_key(i), time.time() - t_start


def cmd_render(pr, budget, crf=18, workers=None):
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    if not cmd_check(pr, quiet=True):
        sys.exit("fix the plan first (render.py plan.json --check)")
    B = Budget(budget)
    os.makedirs(pr.P["chunks"], exist_ok=True)
    n_chunks = math.ceil(pr.n_frames / (CHUNK * FPS))
    state_p = os.path.join(pr.P["chunks"], "state.json")
    state = load_json(state_p, {}) or {}
    per_chunk = state.get("_sec_per_chunk", 25.0)
    todo = [i for i in range(n_chunks)
            if state.get(str(i)) != pr.chunk_key(i) or not os.path.exists(os.path.join(pr.P["chunks"], f"c{i:04d}.mp4"))]
    workers = workers or max(1, min(4, (os.cpu_count() or 2)))
    if todo:
        print(f"rendering {len(todo)}/{n_chunks} chunks with {workers} workers")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        running = set()
        queue = list(todo)
        while queue or running:
            while queue and len(running) < workers and B.ok(per_chunk * 1.4 + 5):
                running.add(ex.submit(_render_chunk, pr.plan_path, queue.pop(0), crf))
            if not running:
                break
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for fu in done:
                i, key, dt = fu.result()
                state[str(i)] = key
                per_chunk = 0.6 * per_chunk + 0.4 * dt
                state["_sec_per_chunk"] = per_chunk
                save_json(state_p, state)
                print(f"chunk {i + 1}/{n_chunks} ({dt:.1f}s)")
        if queue:
            print(f"BUDGET: {n_chunks - len(queue)}/{n_chunks} chunks ready. Re-run the same command to continue.")
            return False
    # ---- concat + audio
    lst = os.path.join(pr.P["chunks"], "list.txt")
    with open(lst, "w") as fh:
        for i in range(n_chunks):
            fh.write(f"file 'c{i:04d}.mp4'\n")
    video = os.path.join(pr.P["work"], "video_only.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video],
                   check=True)
    mix_wav = os.path.join(pr.P["work"], "mix.wav")
    log = audio.mix(pr.plan, pr.elements, pr.timeline, pr.P["voice"] if pr.meta.get("has_audio") else None,
                    mix_wav, os.path.join(pr.P["work"], "sfx_only.wav"))
    save_json(os.path.join(pr.P["work"], "sfx_log.json"), log)
    os.makedirs(os.path.dirname(pr.plan["output"]), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", mix_wav, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{pr.dur:.3f}", "-movflags", "+faststart",
                    pr.plan["output"]], check=True)
    print(f"DONE {pr.plan['output']}  ({pr.dur:.2f}s, {len(log)} sfx; SFX-only stem: "
          f"{os.path.join(pr.P['work'], 'sfx_only.wav')})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--preview", help="comma separated seconds, or 'auto'")
    ap.add_argument("--safe", action="store_true", help="draw the Instagram safe zone on previews")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--fit-safe", action="store_true", help="auto-move/shrink elements into the safe zone")
    ap.add_argument("--budget", type=float, default=240)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--workers", type=int, default=0, help="parallel chunk renderers (default: up to 4)")
    a = ap.parse_args()
    if a.fit_safe:
        fit_safe(a.plan)
    pr = Project(a.plan)
    if a.check:
        sys.exit(0 if cmd_check(pr) else 1)
    if a.preview:
        if a.preview == "auto":
            ts = sorted({round(e["start"] + min(0.6, (e["end"] - e["start"]) / 2), 2) for e in pr.elements
                         if not e.get("_from_subs")})[:12] or [pr.dur / 2]
        else:
            ts = [float(x) for x in a.preview.split(",")]
        cmd_preview(pr, ts, a.safe)
    if a.render:
        cmd_render(pr, a.budget, a.crf, a.workers or None)


if __name__ == "__main__":
    main()
