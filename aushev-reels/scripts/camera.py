"""Virtual camera over the prepared footage.

* Timeline: optional `video.keep` segments (jump cuts) -> output time <-> source time.
* Eye line: the speaker's eyes stay on ONE horizontal line for the whole video. The framing size
  (крупность) is a single constant scale for the whole video; only vertical/horizontal shifts follow
  the eyes. Zooms are added only where the plan asks, centred on the eyes (so the line holds).
* Transitions (Mister Horse / Animation Composer set): zoom_in, zoom_out, whip (left/right/up/down),
  slide, spin, glitch, flash, blur, shake, luma (light leak). Mirror-edge padding like AC.
"""
import math

import cv2
import numpy as np

from common import FPS, H, W, EASES, clamp, ease_in_expo, ease_out_expo, spring_tail, hex_rgb

GEO_TRANS = ("whip", "slide", "zoom_in", "zoom_out", "spin", "blur")


class Timeline:
    def __init__(self, keep, src_dur):
        keep = keep or [[0.0, src_dur]]
        self.keep = [[max(0.0, float(a)), min(src_dur, float(b))] for a, b in keep if b > a]
        self.out_starts = []
        acc = 0.0
        for a, b in self.keep:
            self.out_starts.append(acc)
            acc += b - a
        self.dur = acc

    def to_src(self, t):
        for (a, b), o in zip(self.keep, self.out_starts):
            if t < o + (b - a) or (a, b) == tuple(self.keep[-1]):
                return a + (t - o)
        return self.keep[-1][1]

    def to_out(self, ts):
        """source time -> output time (None if cut away)."""
        for (a, b), o in zip(self.keep, self.out_starts):
            if a - 1e-6 <= ts <= b + 1e-6:
                return o + (ts - a)
        return None

    def cuts(self, src_cuts):
        out = [o for o in self.out_starts[1:]]
        for c in src_cuts or []:
            oc = self.to_out(c)
            if oc is not None:
                out.append(oc)
        return sorted(set(round(c, 3) for c in out))


class Camera:
    def __init__(self, plan, meta, track, timeline):
        self.v = plan.get("video", {})
        self.meta, self.tl = meta, timeline
        self.pw, self.ph = meta["pw"], meta["ph"]
        n = meta["frames"]
        if track:
            self.ex = np.array(track["eye_x"], np.float32)
            self.ey = np.array(track["eye_y"], np.float32)
        else:
            self.ex = np.full(n, self.pw / 2, np.float32)
            self.ey = np.full(n, self.ph * 0.36, np.float32)
        # base scale: cover-fit of the prepared source (= 1/OVERSCAN for 9:16 sources)
        cover = max(W / self.pw, H / self.ph)
        # eyes used by the output timeline (jump-cut parts excluded)
        idx = [min(n - 1, int(round(self.tl.to_src(t) * FPS))) for t in np.arange(0, self.tl.dur, 1 / FPS)]
        ey_used = self.ey[idx] if n and idx else self.ey
        lo_e, hi_e = (float(np.percentile(ey_used, 2)), float(np.percentile(ey_used, 98))) if len(ey_used) else (
            self.ph * .36, self.ph * .36)
        # eye-line target (output px): by default the line that needs the smallest constant reframe,
        # kept in the upper part of the frame (where eyes of a talking head naturally sit)
        line = self.v.get("eye_line", "auto")
        if line == "auto":
            self.eye_y = clamp(H * lo_e / (lo_e + self.ph - hi_e), H * 0.27, H * 0.42)
        else:
            self.eye_y = float(line) * H if float(line) <= 1 else float(line)
        # minimal constant scale that keeps eyes on the line without showing borders
        need = cover
        if lo_e > 1:
            need = max(need, self.eye_y / lo_e, (H - self.eye_y) / max(self.ph - hi_e, 1))
        max_scale = cover * float(self.v.get("max_reframe", 1.15))
        self.base = min(need, max_scale) * float(self.v.get("base_zoom", 1.0))
        self.eye_line_ok = need <= max_scale + 1e-6
        self.zooms = plan.get("zooms", [])
        self.trans = plan.get("transitions", [])
        g = self.v.get("grade", {})
        self.grade = g if g is not False else None
        self._vign = None
        self._grade_m = None
        self.horizontal_follow = self.pw * self.base > W * 1.35   # wide sources: follow the face in x

    # ---------------------------------------------------------------- per-frame geometry
    def eye_at(self, ts):
        i = int(clamp(round(ts * FPS), 0, len(self.ey) - 1))
        return float(self.ex[i]), float(self.ey[i])

    def zoom_at(self, t):
        z = 1.0
        for zm in self.zooms:
            s, e = zm["start"], zm["end"]
            if t < s - 1e-6 or t > e + float(zm.get("out", 0.3)) + 1.0:
                continue
            amt = float(zm.get("scale", 1.12)) - 1.0
            kind = zm.get("ease", "smooth")
            ti, to = float(zm.get("in", 0.12 if kind == "punch" else 0.35)), float(zm.get("out", 0.3))
            if kind == "punch":
                env = EASES["expo"](clamp((t - s) / ti))
                if t > s + ti:
                    env += spring_tail(1.0 / max(ti, 1e-3), t - s - ti)
            elif kind == "creep":     # slow push across the whole range
                env = EASES["inout"]((t - s) / max(e - s, 1e-3))
            else:
                env = EASES["out"]((t - s) / ti)
            if t > e:
                env *= 0 if to <= 0 else 1 - EASES["inout"]((t - e) / to)
            z *= 1 + amt * env
        return z

    def base_matrix(self, t):
        ts = self.tl.to_src(t)
        ex, ey = self.eye_at(ts)
        s = self.base * self.zoom_at(t)
        # zoom is centred on the eyes: at rest x keeps the original framing (or follows the face for
        # wide sources); y always puts the eyes on the fixed eye line.
        ex_out = W / 2 if self.horizontal_follow else self.base * (ex - self.pw / 2) + W / 2
        M = np.array([[s, 0, ex_out - s * ex], [0, s, self.eye_y - s * ey], [0, 0, 1]], np.float64)
        # clamp so the footage always covers the frame
        sw, sh = self.pw * s, self.ph * s
        M[0, 2] = clamp(M[0, 2], W - sw, 0)
        M[1, 2] = clamp(M[1, 2], H - sh, 0)
        return M

    # ---------------------------------------------------------------- transitions
    def transition_at(self, t):
        """Returns list of (kind, phase p in [-1,1], params) for active transitions."""
        act = []
        for tr in self.trans:
            d = float(tr.get("dur", 0.36))
            T = float(tr["at"])
            if T - d / 2 <= t <= T + d / 2:
                act.append((tr.get("type", "whip"), clamp((t - T) / (d / 2), -1.0, 1.0), tr))
        return act

    @staticmethod
    def trans_matrix(kind, p, tr):
        """Output-space 3x3 transform for geometric transitions at phase p (-1..0 outgoing, 0..1 incoming)."""
        cx, cy = W / 2, H / 2
        s, rot, dx, dy = 1.0, 0.0, 0.0, 0.0
        a = ease_in_expo(1 + p) if p < 0 else 0.0           # outgoing accelerates
        b = 1 - ease_out_expo(p) if p >= 0 else 0.0         # incoming decelerates
        if kind in ("whip", "slide"):
            vx, vy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[tr.get("dir", "left")]
            off = a if p < 0 else -b
            dx, dy = vx * W * off, vy * H * off
        elif kind == "zoom_in":
            s = 1 + 1.2 * a if p < 0 else 1 - 0.35 * b
        elif kind == "zoom_out":
            s = 1 - 0.4 * a if p < 0 else 1 + 1.0 * b
        elif kind == "spin":
            sign = -1 if tr.get("dir", "right") == "left" else 1
            rot = sign * (70 * a if p < 0 else -70 * b)
            s = 1 + 0.25 * (a if p < 0 else b)
        elif kind == "blur":
            s = 1 + 0.06 * (a if p < 0 else b)
        M = cv2.getRotationMatrix2D((cx, cy), rot, s)
        M[0, 2] += dx
        M[1, 2] += dy
        return np.vstack([M, [0, 0, 1]])

    def render_frame(self, src_frames_getter, t):
        """src_frames_getter(ts) -> uint8 RGB prepared frame. Returns float32 RGB 0..1 (H,W,3)."""
        trs = self.transition_at(t)
        geo = [tr for tr in trs if tr[0] in GEO_TRANS]
        img = src_frames_getter(self.tl.to_src(t))
        Mb = self.base_matrix(t)
        if geo:
            kind, p, tr = geo[0]
            d = float(tr.get("dur", 0.36))
            # motion blur: sub-samples across the frame interval
            nsub = int(tr.get("samples", 7 if kind in ("whip", "zoom_in", "zoom_out", "spin") else 3))
            span = (1.0 / FPS) / (d / 2)
            acc = None
            for k in range(nsub):
                pk = clamp(p + span * (k / max(1, nsub - 1) - 0.5), -1, 1)
                if (pk < 0) != (p < 0):        # never blend across the cut
                    pk = -1e-4 if p < 0 else 0.0
                M = (self.trans_matrix(kind, pk, tr) @ Mb)[:2]
                fr = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
                acc = fr.astype(np.float32) if acc is None else acc + fr
            out = acc / (nsub * 255.0)
        else:
            out = cv2.warpAffine(img, Mb[:2], (W, H), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REFLECT_101).astype(np.float32) / 255.0
        for kind, p, tr in trs:
            out = self.post_transition(out, kind, p, tr, t)
        return self.apply_grade(out)

    @staticmethod
    def post_transition(img, kind, p, tr, t):
        w = max(0.0, 1 - abs(p))                           # 1 at the cut point
        if kind == "flash":
            c = np.array(hex_rgb(tr.get("color"), (255, 255, 255)), np.float32) / 255
            k = float(tr.get("strength", 0.85)) * w ** 2
            img = img * (1 - k) + c * k
        elif kind == "luma":       # light leak: warm soft blob sweeping in from a corner, screen blend
            c = np.array(hex_rgb(tr.get("color"), (255, 170, 90)), np.float32) / 255
            k = float(tr.get("strength", 0.85)) * w ** 1.2
            ph = (p + 1) / 2                                   # 0..1 across the window
            cx, cy = W * (1.15 - 0.9 * ph), H * (0.05 + 0.5 * ph)
            ys, xs = np.ogrid[0:H:4, 0:W:4]
            r2 = ((xs - cx) / (W * 0.75)) ** 2 + ((ys - cy) / (H * 0.55)) ** 2
            leak = cv2.resize(np.exp(-r2 * 2.2).astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
            m = cv2.merge([leak * float(c[0] * k), leak * float(c[1] * k), leak * float(c[2] * k)])
            img = 1 - (1 - img) * (1 - m)
        elif kind == "blur":
            img = cv2.GaussianBlur(img, (0, 0), 1 + 28 * w ** 1.5)
        elif kind == "glitch":
            rng = np.random.default_rng(int(t * 1000))
            amt = w * float(tr.get("strength", 1.0))
            sh = int(36 * amt)
            out = img.copy()
            out[..., 0] = np.roll(img[..., 0], sh, 1)
            out[..., 2] = np.roll(img[..., 2], -sh, 1)
            for _ in range(int(10 * amt)):
                y0 = int(rng.integers(0, H - 40))
                hgt = int(rng.integers(8, 90))
                out[y0:y0 + hgt] = np.roll(out[y0:y0 + hgt], int(rng.integers(-120, 120) * amt), 1)
            img = out
        elif kind == "shake" and p >= 0:
            a = float(tr.get("strength", 1.0)) * 26 * math.exp(-5 * p)
            dx, dy = a * math.sin(p * 41), a * math.cos(p * 33)
            M = np.float32([[1.03, 0, dx - W * 0.015], [0, 1.03, dy - H * 0.015]])
            img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT_101)
        return img

    def apply_grade(self, img):
        g = self.grade
        if not g:
            return img
        if self._grade_m is None:
            c = float(g.get("contrast", 1.06))
            sat = float(g.get("saturation", 1.06))
            lumw = np.array([0.299, 0.587, 0.114], np.float32)
            S = np.eye(3, dtype=np.float32) * sat + (1 - sat) * np.tile(lumw, (3, 1))   # saturation matrix
            M = np.zeros((3, 4), np.float32)
            M[:, :3] = S * c
            M[:, 3] = 0.5 - 0.5 * c                                                   # contrast around mid-grey
            self._grade_m = M
            v = float(g.get("vignette", 0.18))
            ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
            r = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
            vg = (1 - v * np.clip(r, 0, 1) ** 2.2).astype(np.float32)
            self._vign = cv2.merge([vg, vg, vg])
        img = cv2.transform(np.ascontiguousarray(img, np.float32), self._grade_m)
        cv2.multiply(img, self._vign, dst=img)
        return np.clip(img, 0, 1, out=img)
