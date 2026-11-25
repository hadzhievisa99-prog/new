#!/usr/bin/env python3
"""Step 1: prepare a phone video for montage.

  python3 prepare.py input.mp4 --work work [--budget 240]

* converts to constant 30 fps, keeps a 1.25x 'overscan' resolution (vertical side = 2400 px)
  so zooms and eye-line shifts stay sharp;
* encodes in 20-second parts -> resumable: re-run the same command until it prints DONE;
* extracts the voice track (48 kHz wav, untouched);
* detects hard cuts (scene changes) -> meta.json["cuts"].
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FPS, H, OVERSCAN, W, Budget, load_json, save_json, work_paths  # noqa: E402

PART = 20.0


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", path], capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(v.get("tags", {}).get("rotate", rot) or rot)
    if abs(rot) % 180 == 90:
        w, h = h, w
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    return {"w": w, "h": h, "dur": dur, "has_audio": bool(a)}


def target_size(w, h):
    """Cover the 9:16 frame at OVERSCAN; keep the source aspect (wide sources keep their width)."""
    th = int(H * OVERSCAN)                       # 2400
    tw = int(round(w * th / h / 2) * 2)
    if tw < int(W * OVERSCAN):                    # narrower than 9:16 -> fit width instead
        tw = int(W * OVERSCAN)
        th = int(round(h * tw / w / 2) * 2)
    return tw, th


def detect_cuts(path, dur, thr=0.32):
    """Scene-change detection on a small proxy (fast)."""
    cmd = ["ffmpeg", "-v", "info", "-i", path, "-vf",
           f"scale=180:-2,select='gt(scene,{thr})',showinfo", "-an", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    cuts = [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", err)]
    out = []
    for c in cuts:
        if 0.3 < c < dur - 0.3 and (not out or c - out[-1] > 0.5):
            out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--work", default="work")
    ap.add_argument("--budget", type=float, default=240)
    ap.add_argument("--crf", type=int, default=16)
    a = ap.parse_args()
    B = Budget(a.budget)
    P = work_paths(a.work)
    os.makedirs(a.work, exist_ok=True)
    meta = load_json(P["meta"], {}) or {}
    if meta.get("input") != os.path.abspath(a.input):
        info = probe(a.input)
        tw, th = target_size(info["w"], info["h"])
        meta = {"input": os.path.abspath(a.input), **info, "pw": tw, "ph": th, "fps": FPS, "parts_done": []}
        save_json(P["meta"], meta)
    dur, tw, th = meta["dur"], meta["pw"], meta["ph"]
    parts_dir = os.path.join(a.work, "prep_parts")
    os.makedirs(parts_dir, exist_ok=True)
    n_parts = max(1, int((dur - 1e-3) // PART) + 1)

    # voice
    if meta["has_audio"] and not os.path.exists(P["voice"]):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.input, "-vn", "-ac", "2", "-ar", "48000",
                        "-c:a", "pcm_s16le", P["voice"]], check=True)

    for i in range(n_parts):
        out = os.path.join(parts_dir, f"part_{i:03d}.mp4")
        if i in meta["parts_done"] and os.path.exists(out):
            continue
        if not B.ok(PART * 2.5) and meta["parts_done"]:
            print(f"BUDGET: {len(meta['parts_done'])}/{n_parts} parts ready. Re-run the same command to continue.")
            return
        t0 = i * PART
        vf = f"fps={FPS},scale={tw}:{th}:flags=lanczos,setsar=1,format=yuv420p"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{PART:.3f}", "-i", a.input,
                        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(a.crf),
                        "-g", str(FPS), "-keyint_min", str(FPS), out], check=True)
        meta["parts_done"] = sorted(set(meta["parts_done"] + [i]))
        save_json(P["meta"], meta)
        print(f"part {i + 1}/{n_parts} ok")

    if not os.path.exists(P["src"]) or meta.get("joined") != n_parts:
        lst = os.path.join(parts_dir, "list.txt")
        with open(lst, "w") as f:
            for i in range(n_parts):
                f.write(f"file 'part_{i:03d}.mp4'\n")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", P["src"]], check=True)
        meta["joined"] = n_parts
        # real frame count of the prepared source
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                              "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", P["src"]],
                             capture_output=True, text=True).stdout.strip()
        meta["frames"] = int(out or round(dur * FPS))
        meta["dur"] = meta["frames"] / FPS
        save_json(P["meta"], meta)

    if "cuts" not in meta:
        meta["cuts"] = detect_cuts(P["src"], meta["dur"])
        save_json(P["meta"], meta)
    print(f"DONE prepared {meta['pw']}x{meta['ph']} @{FPS}fps, {meta['dur']:.2f}s, "
          f"cuts={meta['cuts']}, voice={'yes' if meta['has_audio'] else 'no'}")


if __name__ == "__main__":
    main()
