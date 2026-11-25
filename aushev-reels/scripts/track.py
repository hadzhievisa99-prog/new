#!/usr/bin/env python3
"""Step 3: track the speaker's face / eyes -> work/track.json.

  python3 track.py --work work [--budget 240] [--step 2]

OpenCV Haar cascades (shipped inside opencv-python, no downloads). Every `step`-th frame is
analysed on a 400-px proxy; results are cached in work/track_raw.npz so the command can be
re-run until DONE. Output per frame (prepared-source pixels): eye-centre x/y and eye distance,
filled and smoothed inside each shot (never across a cut).
"""
import argparse
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FPS, Budget, load_json, save_json, work_paths  # noqa: E402

try:
    import cv2
    _OK = hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data")
except ImportError:
    _OK = False
if not _OK:   # OpenCV 5 removed Haar cascades -> install 4.x once and restart this script
    if os.environ.get("AUSHEV_CV_RETRY"):
        sys.exit("OpenCV with Haar cascades is unavailable: pip install 'opencv-python-headless<5'")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--force-reinstall", "--no-deps",
                    "opencv-python-headless>=4.8,<5"], check=False)
    os.environ["AUSHEV_CV_RETRY"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

PROXY_H = 720   # analysis height: small faces in wide/landscape shots stay detectable


def detectors():
    d = cv2.data.haarcascades
    return (cv2.CascadeClassifier(d + "haarcascade_frontalface_default.xml"),
            cv2.CascadeClassifier(d + "haarcascade_eye_tree_eyeglasses.xml"),
            cv2.CascadeClassifier(d + "haarcascade_profileface.xml"))


def detect(gray, face_c, eye_c, prof_c, prev):
    faces = face_c.detectMultiScale(gray, 1.1, 5, minSize=(gray.shape[0] // 18,) * 2)
    if len(faces) == 0:
        faces = prof_c.detectMultiScale(gray, 1.1, 5, minSize=(gray.shape[0] // 18,) * 2)
    if len(faces) == 0:
        return None
    if prev is not None:   # prefer the face closest to the previous one, then the largest
        faces = sorted(faces, key=lambda f: (abs(f[0] + f[2] / 2 - prev[0]) + abs(f[1] + f[3] / 2 - prev[1])) - 2 * f[2])
    else:
        faces = sorted(faces, key=lambda f: -f[2] * f[3])
    x, y, w, h = faces[0]
    ex, ey, ed = x + w / 2, y + 0.40 * h, 0.42 * w
    roi = gray[y:y + int(h * 0.6), x:x + w]
    eyes = eye_c.detectMultiScale(roi, 1.1, 4, minSize=(w // 8, w // 8), maxSize=(w // 3, w // 3))
    if len(eyes) >= 2:
        eyes = sorted(eyes, key=lambda e: -e[2])[:2]
        c = [(x + e[0] + e[2] / 2, y + e[1] + e[3] / 2) for e in eyes]
        if abs(c[0][1] - c[1][1]) < 0.15 * h and abs(c[0][0] - c[1][0]) > 0.2 * w:
            ex, ey = (c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2
            ed = abs(c[0][0] - c[1][0])
    return ex, ey, ed, w


def smooth(arr, valid, sigma):
    idx = np.arange(len(arr))
    if valid.sum() == 0:
        return None
    filled = np.interp(idx, idx[valid], arr[valid])
    # median against outliers, then gaussian
    k = 7
    pad = np.pad(filled, k, mode="edge")
    med = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(filled))])
    r = int(3 * sigma)
    g = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    g /= g.sum()
    return np.convolve(np.pad(med, r, mode="edge"), g, "valid")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work")
    ap.add_argument("--budget", type=float, default=240)
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--smooth", type=float, default=0.35, help="smoothing sigma in seconds")
    ap.add_argument("--set", action="append", default=[],
                    help="manual eyes for a source time range (cartoons, masks, no face): "
                         "START-END:X,Y,D in prepared-source px; saved in work/track_overrides.json")
    a = ap.parse_args()
    P = work_paths(a.work)
    meta = load_json(P["meta"])
    B = Budget(a.budget)
    N, pw, ph = meta["frames"], meta["pw"], meta["ph"]
    k = min(1.0, PROXY_H / ph)
    qh = int(round(ph * k / 2) * 2)
    PROXY_W = int(round(pw * k / 2) * 2)
    raw_p = os.path.join(a.work, "track_raw.npz")
    if os.path.exists(raw_p):
        z = np.load(raw_p)
        data, done = z["data"], int(z["done"])
    else:
        data, done = np.full((N, 4), np.nan, np.float32), 0
    face_c, eye_c, prof_c = detectors()
    if done < N:
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{done / FPS:.4f}", "-i", P["src"],
                                 "-vf", f"scale={PROXY_W}:{qh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        fs = PROXY_W * qh
        prev = None
        f = done
        while f < N:
            buf = proc.stdout.read(fs)
            if len(buf) < fs:
                break
            if f % a.step == 0:
                g = cv2.equalizeHist(np.frombuffer(buf, np.uint8).reshape(qh, PROXY_W))
                r = detect(g, face_c, eye_c, prof_c, prev)
                if r:
                    prev = r[:2]
                    data[f] = [r[0] / k, r[1] / k, r[2] / k, r[3] / k]
            f += 1
            if f % 60 == 0 and not B.ok(5):
                break
        proc.kill()
        done = f
        np.savez(raw_p, data=data, done=done)
        if done < N:
            print(f"BUDGET: tracked {done}/{N} frames. Re-run the same command to continue.")
            return

    cuts = [0] + [int(round(c * FPS)) for c in meta.get("cuts", [])] + [N]
    out = np.zeros((N, 3), np.float32)
    found = ~np.isnan(data[:, 0])
    for s, e in zip(cuts, cuts[1:]):
        if e <= s:
            continue
        seg, v = data[s:e].copy(), found[s:e].copy()
        if v.sum() >= 3:   # reject outliers (other 'faces', transition garbage) far from the shot median
            med = np.nanmedian(seg[:, :3], 0)
            dist = np.hypot(seg[:, 0] - med[0], seg[:, 1] - med[1])
            bad = v & ((dist > 1.2 * med[2]) | (np.abs(seg[:, 2] - med[2]) > 0.5 * med[2]))
            v &= ~bad
            seg[bad] = np.nan
        cols = []
        for c in range(3):
            sm = smooth(seg[:, c], v, a.smooth * FPS / 1.0) if v.sum() >= 2 else None
            if sm is None:  # no face in this shot -> fall back to typical talking-head layout
                sm = np.full(e - s, [pw / 2, ph * 0.36, pw * 0.11][c], np.float32)
            cols.append(sm)
        out[s:e] = np.stack(cols, 1)
    ov_p = os.path.join(a.work, "track_overrides.json")
    overrides = load_json(ov_p, []) or []
    for spec in a.set:
        rng, vals = spec.split(":")
        t0, t1 = (float(x) for x in rng.split("-"))
        x, y, d = (float(v) for v in vals.split(","))
        overrides = [o for o in overrides if not (o["start"] == t0 and o["end"] == t1)]
        overrides.append({"start": t0, "end": t1, "x": x, "y": y, "d": d})
    if a.set:
        save_json(ov_p, overrides)
    for o in overrides:
        f0, f1 = int(round(o["start"] * FPS)), min(N, int(round(o["end"] * FPS)))
        out[f0:f1] = [o["x"], o["y"], o["d"]]
    rate = float(found[::a.step].mean()) if N else 0
    save_json(P["track"], {"fps": FPS, "frames": N, "detect_rate": round(rate, 3),
                           "eye_x": [round(float(v), 1) for v in out[:, 0]],
                           "eye_y": [round(float(v), 1) for v in out[:, 1]],
                           "eye_d": [round(float(v), 1) for v in out[:, 2]]})
    print(f"DONE face found on {rate * 100:.0f}% of analysed frames; median eye y = "
          f"{np.median(out[:, 1]):.0f}px of {ph}")


if __name__ == "__main__":
    main()
