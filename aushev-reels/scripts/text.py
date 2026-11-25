"""Text layout + AUSHEV text animators (word / letter / word_bounce x up/down/left/right x in/out/both).

Mapping to the AE presets (decoded from the .ffx files inside aushev.ae_v1.jsx):
* Word_<Dir> / Letter-by-letter_<Dir>: Range Selector (Units %, Based On Words / Characters,
  Shape Ramp Up, Ease High -50), Offset keyed -100 -> 100; animator props: Position 100 px in the
  direction, Opacity 0. -> each unit eases from (offset=dist, opacity 0) to rest, units staggered.
* Word_<Dir>_Bounce: Expression Selector, amount = 150*cos(2.5*t*2pi)/exp(7t) with
  t = (time - inPoint) - Speed*textIndex (Speed = 0.1 s); Position offset from sliders (44 px), Opacity 0.
* OUT = convertToOut: keys mirrored in time and (time - inPoint) -> (outPoint - time), so OUT is the exact
  time-mirror of IN ending on the element's out-point. IN and OUT live on one element together.
"""
import math
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import (EASES, WB_AMP, WB_DECAY, WB_DELAY, WB_FREQ, bounce_state, clamp, font_path, hex_rgb)
import fx

DIRS = {"up": (0, 1), "down": (0, -1), "left": (1, 0), "right": (-1, 0)}   # start offset (unit vector)
_FONT_CACHE = {}


def get_font(weight, size):
    k = (weight, int(size))
    if k not in _FONT_CACHE:
        _FONT_CACHE[k] = ImageFont.truetype(font_path(weight), int(size))
    return _FONT_CACHE[k]


def norm_word(w):
    return re.sub(r"[^\wёЁ%$€]+", "", w.lower())


class Layout:
    """Word-wrapped, aligned text. Units are words (with per-letter sub positions)."""

    def __init__(self, text, size=84, weight="extrabold", max_width=900, align="center",
                 line_height=1.1, tracking=0.0, upper=False):
        self.text = text.upper() if upper else text
        self.size, self.weight = size, weight
        self.font = f = get_font(weight, size)
        self.tracking = tracking
        asc, desc = f.getmetrics()
        self.asc, self.desc = asc, desc
        self.lh = size * line_height
        words = self.text.split()
        space = f.getlength(" ") + tracking
        lines = self._wrap(words, max_width, space)
        if len(lines) > 1:            # balance: narrowest width that keeps the same number of lines
            lo, hi = max(self.word_width(w) for w in words), max_width
            for _ in range(14):
                mid = (lo + hi) / 2
                if len(self._wrap(words, mid, space)) == len(lines):
                    hi = mid
                else:
                    lo = mid
            lines = self._wrap(words, hi + 0.5, space)
        self.units = []          # dict(text, x, y, w, line, index, letters=[(ch, dx)])
        widths = [sum(w for _, w in ln) + space * (len(ln) - 1) for ln in lines]
        self.w = max(widths) if widths else 0
        self.h = self.lh * (len(lines) - 1) + asc + desc if lines else 0
        idx = 0
        for li, (ln, lw) in enumerate(zip(lines, widths)):
            x = {"left": 0, "right": self.w - lw}.get(align, (self.w - lw) / 2)
            y = li * self.lh
            for wd, ww in ln:
                letters, lx = [], 0.0
                for ci, ch in enumerate(wd):
                    letters.append((ch, lx))
                    lx = f.getlength(wd[:ci + 1]) + tracking * (ci + 1)
                self.units.append({"text": wd, "x": x, "y": y, "w": ww, "line": li, "index": idx,
                                   "letters": letters})
                idx += 1
                x += ww + space
        self.n_lines = len(lines)

    def _wrap(self, words, max_width, space):
        lines, cur, cur_w = [], [], 0.0
        for wd in words:
            ww = self.word_width(wd)
            if cur and cur_w + space + ww > max_width:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append((wd, ww))
            cur_w += (space if len(cur) > 1 else 0) + ww
        if cur:
            lines.append(cur)
        return lines

    def word_width(self, wd):
        return self.font.getlength(wd) + self.tracking * max(0, len(wd) - 1)


def render_sprite(text, font, color, pad, stroke=0, stroke_color=(0, 0, 0)):
    """Premultiplied float RGBA sprite of a text string; origin at (pad, pad) = top of ascent."""
    w = int(font.getlength(text)) + 2 * pad + 4
    asc, desc = font.getmetrics()
    h = asc + desc + 2 * pad
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((pad, pad), text, font=font, fill=tuple(color) + (255,), stroke_width=stroke,
           stroke_fill=tuple(stroke_color) + (255,))
    a = np.asarray(im, np.float32) / 255.0
    a[..., :3] *= a[..., 3:4]
    return a


# ------------------------------------------------------------------ animation math
def unit_state_in(mode, tau, dur, dist, ease):
    """State of one unit tau seconds after its own start (IN direction).
    Returns (offset_factor, opacity, scale)."""
    if mode == "word_bounce":
        if tau < 0:
            amount = 1.0
        else:
            amount = WB_AMP * math.cos(WB_FREQ * tau * 2 * math.pi) / math.exp(WB_DECAY * tau)
        return amount * dist, clamp(1 - amount), 1.0
    p = EASES.get(ease, EASES["out"])(clamp(tau / max(dur, 1e-3)))
    return (1 - p) * dist, p, 1.0


class TextAnimator:
    """Per-unit states for an element at time t."""

    def __init__(self, el, layout):
        a = el.get("anim") or {}
        self.mode = a.get("mode", "word")
        self.dir = DIRS.get(a.get("dir", "up"), (0, 1))
        self.out_dir = DIRS.get(a.get("out_dir", a.get("dir", "up")), self.dir)
        self.io = a.get("io", "both")
        size = layout.size
        dflt_dist = {"word": 0.9, "letter": 0.7, "word_bounce": 0.55}.get(self.mode, 0.9) * size
        self.dist = float(a.get("dist", dflt_dist))
        self.dur = float(a.get("dur", {"word": 0.45, "letter": 0.32, "word_bounce": 0.6}.get(self.mode, 0.45)))
        self.stagger = float(a.get("stagger", {"word": 0.07, "letter": 0.028, "word_bounce": WB_DELAY}
                                   .get(self.mode, 0.07)))
        self.ease = a.get("ease", "out")
        self.start, self.end = el["start"], el["end"]
        self.times = a.get("times")       # absolute IN start per word (speech sync)
        # unit list: words or letters
        self.units = []
        for u in layout.units:
            if self.mode == "letter":
                for li, (ch, dx) in enumerate(u["letters"]):
                    if ch.strip():
                        self.units.append((u["index"], li))
            else:
                self.units.append((u["index"], None))
        self.n = len(self.units)
        # IN start time of each unit
        self.t_in = []
        letter_counter = 0
        for (wi, li) in self.units:
            if self.times and wi < len(self.times):
                base = self.times[wi]
                if li is not None:
                    base += li * self.stagger
                self.t_in.append(base)
            else:
                k = letter_counter if li is not None else wi
                self.t_in.append(self.start + k * self.stagger)
            letter_counter += 1 if li is not None else 0
        self.total_in = (max(self.t_in) - self.start if self.t_in else 0) + self.dur
        self.total_out = (self.n - 1) * self.stagger + self.dur

    def states(self, t):
        """List of (dx, dy, opacity) per unit."""
        res = []
        in_on = self.io in ("in", "both")
        out_on = self.io in ("out", "both")
        for i, (wi, li) in enumerate(self.units):
            dx = dy = 0.0
            op = 1.0
            if in_on:
                off, o, _ = unit_state_in(self.mode, t - self.t_in[i], self.dur, self.dist, self.ease)
                dx += self.dir[0] * off
                dy += self.dir[1] * off
                op *= o
            if out_on:
                # convertToOut: (time - inPoint) -> (outPoint - time); unit order mirrored as in AE
                tm = self.start + (self.end - t)
                off, o, _ = unit_state_in(self.mode, tm - (self.start + i * self.stagger), self.dur,
                                          self.dist, self.ease)
                dx += self.out_dir[0] * off
                dy += self.out_dir[1] * off
                op *= o
            res.append((dx, dy, op))
        return res


# ------------------------------------------------------------------ element renderer
class TextElement:
    """Renders text/card/counter elements with anim + bounce + glass + glow + shadow."""

    def __init__(self, el):
        self.el = el
        self.size = float(el.get("size", 84))
        self.color = hex_rgb(el.get("color"), (255, 255, 255))
        self.accent_color = hex_rgb(el.get("accent_color"), (255, 210, 63))
        self.accent = {norm_word(w) for w in el.get("accent", [])}
        self.karaoke = bool(el.get("karaoke"))
        self.word_times = (el.get("anim") or {}).get("times")
        self.word_ends = el.get("word_ends")
        self.glow_on = bool(el.get("glow"))
        self.shadow_on = el.get("shadow", True) is not False and el.get("shadow") is not None
        self.glass_on = bool(el.get("glass"))
        self.gp = fx.glass_params(el.get("glass")) if self.glass_on else None
        self.glp = fx.glow_params(el.get("glow"), self.color) if self.glow_on else None
        self.shp = fx.shadow_params(el.get("shadow"), self.size) if self.shadow_on else None
        self._layouts = {}
        self._sprites = {}
        self.pad = int(self.size * 0.15) + 4

    # layout per string (counter changes text every frame)
    def layout(self, text):
        if text not in self._layouts:
            e = self.el
            self._layouts[text] = Layout(text, self.size, e.get("weight", "extrabold"), e.get("max_width", 900),
                                         e.get("align", "center"), e.get("line_height", 1.08),
                                         e.get("tracking", 0.0), e.get("upper", False))
        return self._layouts[text]

    def sprite(self, text, color):
        k = (text, color)
        if k not in self._sprites:
            st = self.el.get("stroke", 0)
            self._sprites[k] = render_sprite(text, get_font(self.el.get("weight", "extrabold"), self.size),
                                             color, self.pad, st, hex_rgb(self.el.get("stroke_color"), (0, 0, 0)))
        return self._sprites[k]

    def current_text(self, t):
        e = self.el
        if e["type"] == "counter":
            cd = float(e.get("count_dur", 1.2))
            st = e["start"] + float(e.get("count_delay", 0.15))
            p = EASES["expo"](clamp((t - st) / cd))
            v = e.get("from", 0) + (e.get("to", 100) - e.get("from", 0)) * p
            dec = int(e.get("decimals", 0))
            num = f"{v:,.{dec}f}".replace(",", " ")
            return f"{e.get('prefix', '')}{num}{e.get('suffix', '')}"
        return e.get("text", "")

    def text_rest_box(self, text=None):
        """(x0, y0, x1, y1) of the laid-out text at rest, in frame coords."""
        lay = self.layout(text if text is not None else self.current_text(self.el["start"] + 10))
        cx, cy = self.el.get("x", 540), self.el.get("y", 960)
        return cx - lay.w / 2, cy - lay.h / 2, cx + lay.w / 2, cy + lay.h / 2

    def glass_box(self, lay):
        e = self.el
        cx, cy = e.get("x", 540), e.get("y", 960)
        pad = self.gp["pad"] if self.gp else [0, 0]
        hw = (e.get("w") or (lay.w + 2 * pad[0])) / 2
        hh = (e.get("h") or (lay.h + 2 * pad[1])) / 2
        return cx, cy, hw, hh

    def card_anim(self, t):
        """Glass plate pop (used when the element has no bounce)."""
        e = self.el
        d = min(0.32, (e["end"] - e["start"]) / 2)
        io = (e.get("anim") or {}).get("io", "both")
        s, op = 1.0, 1.0
        if io in ("in", "both"):
            p = clamp((t - e["start"]) / d)
            s *= 0.86 + 0.14 * EASES["out"](p) + (0.06 * math.sin(1.8 * 2 * math.pi * (t - e["start"] - d)) *
                                                   math.exp(-5 * (t - e["start"] - d)) if t > e["start"] + d else 0)
            op *= EASES["out"](p)
        if io in ("out", "both"):
            p = clamp((e["end"] - t) / d)
            s *= 0.86 + 0.14 * EASES["out"](p)
            op *= EASES["out"](p)
        return s, op

    def draw(self, frame, t):
        e = self.el
        if t < e["start"] or t >= e["end"]:
            return
        text = self.current_text(t)
        lay = self.layout(text)
        cx, cy = e.get("x", 540), e.get("y", 960)
        # ---- element-level bounce (Scale/Rotation) — AUSHEV Bounce
        bscale, brot = 1.0, float(e.get("rotation", 0.0))
        b = e.get("bounce")
        if b:
            bio = b.get("io", "both") if isinstance(b, dict) else (e.get("anim") or {}).get("io", "both")
            bscale, brot = bounce_state(t, e["start"], e["end"], bio, 1.0, brot)
        scale = bscale * float(e.get("scale", 1.0))
        if scale <= 0.005:
            return
        # ---- glass plate
        if self.glass_on:
            gx, gy, hw, hh = self.glass_box(lay)
            if b:
                gs, gop = scale, 1.0
            else:
                gs, gop = self.card_anim(t)
            fx.glass(frame, gx, gy, hw, hh, self.gp, gs, brot, gop)
        if not text.strip():
            return
        # ---- build the text canvas (units with their animation offsets)
        anim = TextAnimator(e, lay)
        states = anim.states(t)
        extra = int(max(abs(anim.dist) * 1.6, 10))
        gpad = 0
        if self.glow_on:
            gpad = int(self.glp["radius"] * 2.2)
        if self.shadow_on:
            gpad = max(gpad, int(self.size * (self.shp["long"] + 0.35)))
        P = self.pad + extra + gpad
        cw, ch = int(lay.w + 2 * P), int(lay.h + 2 * P)
        canvas = np.zeros((ch, cw, 4), np.float32)
        for (wi, li), (dx, dy, op) in zip(anim.units, states):
            if op <= 0.003:
                continue
            u = lay.units[wi]
            col = self.color
            if norm_word(u["text"]) in self.accent and self.accent:
                col = self.accent_color
            if self.karaoke and self.word_times and wi < len(self.word_times):
                if self.word_times[wi] <= t:
                    nxt = self.word_times[wi + 1] if wi + 1 < len(self.word_times) else e["end"]
                    if t < nxt:
                        col = self.accent_color
            if li is None:
                spr = self.sprite(u["text"], col)
                ox = u["x"]
            else:
                ch_, lx = u["letters"][li]
                spr = self.sprite(ch_, col)
                ox = u["x"] + lx
            x = int(round(P + ox + dx - self.pad))
            y = int(round(P + u["y"] + dy - self.pad))
            _over_rgba(canvas, spr, x, y, op)
        # ---- transform (bounce scale/rotation around the element centre) into frame space
        M = cv2.getRotationMatrix2D((cw / 2, ch / 2), -brot, scale)
        M[0, 2] += cx - cw / 2
        M[1, 2] += cy - ch / 2
        # warp only the needed frame ROI
        corners = np.array([[0, 0, 1], [cw, 0, 1], [0, ch, 1], [cw, ch, 1]], np.float32) @ M.T
        x0, y0 = np.floor(corners.min(0)).astype(int)
        x1, y1 = np.ceil(corners.max(0)).astype(int)
        Hf, Wf = frame.shape[:2]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(Wf, x1), min(Hf, y1)
        if x1 <= x0 or y1 <= y0:
            return
        M2 = M.copy()
        M2[0, 2] -= x0
        M2[1, 2] -= y0
        layer = cv2.warpAffine(canvas, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR)
        alpha = layer[..., 3]
        if alpha.max() <= 0.002:
            return
        opacity = float(e.get("opacity", 1.0))
        if self.shadow_on:
            sh = fx.shadow(alpha, self.shp)
            fx.multiply_dark(frame, sh, x0, y0, hex_rgb(self.shp.get("color"), (0, 0, 0)), opacity)
        fx.over(frame, layer, x0, y0, opacity)
        if self.glow_on:
            g = fx.glow(layer, self.glp)
            fx.screen(frame, g, x0, y0, self.glp.get("opacity", 1.0) * opacity)

    # -------- analytic extents for the safe-zone check
    def extents(self):
        """Max bounding box (frame coords) covered by text/plate across the animation."""
        e = self.el
        texts = [self.current_text(e["start"] + t) for t in (0, 0.3, 0.6, 1.0, 2.0, 99)] \
            if e["type"] == "counter" else [e.get("text", "")]
        box = None
        for txt in texts:
            lay = self.layout(txt)
            if not txt.strip() and not self.glass_on:
                continue
            x0, y0, x1, y1 = self.text_rest_box(txt)
            anim = TextAnimator(e, lay)
            mode = anim.mode
            lo, hi = (-0.4 * anim.dist, 0.85 * anim.dist) if mode == "word_bounce" else (0, 0.85 * anim.dist)
            for dvec in {anim.dir, anim.out_dir}:
                for f_ in (lo, hi):
                    x0 = min(x0, x0 + dvec[0] * f_)
                    x1 = max(x1, x1 + dvec[0] * f_)
                    y0 = min(y0, y0 + dvec[1] * f_)
                    y1 = max(y1, y1 + dvec[1] * f_)
            if self.glass_on:
                gx, gy, hw, hh = self.glass_box(lay)
                x0, y0, x1, y1 = min(x0, gx - hw), min(y0, gy - hh), max(x1, gx + hw), max(y1, gy + hh)
            if e.get("bounce"):
                k = 1.08
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                x0, x1 = mx - (mx - x0) * k, mx + (x1 - mx) * k
                y0, y1 = my - (my - y0) * k, my + (y1 - my) * k
            s = float(e.get("scale", 1.0))
            if s != 1.0:
                mx, my = e.get("x", 540), e.get("y", 960)
                x0, x1 = mx - (mx - x0) * s, mx + (x1 - mx) * s
                y0, y1 = my - (my - y0) * s, my + (y1 - my) * s
            b = (x0, y0, x1, y1)
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        return box


def _over_rgba(canvas, spr, x, y, op):
    h, w = spr.shape[:2]
    r = fx.roi(canvas.shape, x, y, w, h)
    if r is None:
        return
    fx0, fy0, fx1, fy1, sx, sy = r
    src = spr[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0] * op
    dst = canvas[fy0:fy1, fx0:fx1]
    dst *= (1 - src[..., 3:4])
    dst += src
