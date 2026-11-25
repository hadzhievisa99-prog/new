"""Image effects that replace the After Effects plugins.

* glow()    — Deep Glow 2 look: physically-inspired stack of blurs (radius doubles every layer,
              energy falls off), exposure, threshold, inner/outer tint, screen-composited -> no clipping.
* shadow()  — Shadow Studio 3 look: long soft shadow (stepped extrusion along the light direction with
              gradient falloff + big blur) plus a short dense contact shadow.
* glass()   — Liquid Glass look: frosted blur of the footage under a rounded plate, lens refraction
              at the rim (with chromatic aberration), tint, specular rim light from a light angle,
              inner edge line and soft drop shadow.
All images are float32 0..1. RGBA layers are premultiplied.
"""
import math

import cv2
import numpy as np

from common import hex_rgb


# ------------------------------------------------------------------ basics
def blur(img, sigma):
    """Fast large gaussian: downscale, blur, upscale."""
    if sigma <= 0.5:
        return img
    h, w = img.shape[:2]
    k = 1
    while sigma / k > 6 and min(h, w) / (k * 2) > 8:
        k *= 2
    if k > 1:
        small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigma / k)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(img, (0, 0), sigma)


def roi(frame_shape, x, y, w, h):
    """Clip a rectangle to the frame. Returns (fx0, fy0, fx1, fy1, sx0, sy0) or None."""
    H, W = frame_shape[:2]
    fx0, fy0, fx1, fy1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    return fx0, fy0, fx1, fy1, fx0 - x, fy0 - y


def _a3(a):
    return cv2.merge([a, a, a]) if a.ndim == 2 else a


def over(frame, rgba, x, y, opacity=1.0):
    """Premultiplied 'over' composite of rgba (h,w,4) at integer position."""
    h, w = rgba.shape[:2]
    r = roi(frame.shape, x, y, w, h)
    if r is None or opacity <= 0:
        return
    fx0, fy0, fx1, fy1, sx, sy = r
    src = rgba[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
    rgb = np.ascontiguousarray(src[..., :3])
    a = np.ascontiguousarray(src[..., 3])
    if opacity != 1.0:
        rgb = rgb * opacity
        a = a * opacity
    dst = frame[fy0:fy1, fx0:fx1]
    dst[:] = cv2.add(cv2.multiply(dst, _a3(1.0 - a)), rgb)


def screen(frame, rgb, x, y, opacity=1.0):
    h, w = rgb.shape[:2]
    r = roi(frame.shape, x, y, w, h)
    if r is None:
        return
    fx0, fy0, fx1, fy1, sx, sy = r
    src = rgb[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
    if opacity != 1.0:
        src = src * opacity
    dst = frame[fy0:fy1, fx0:fx1]
    dst[:] = 1.0 - cv2.multiply(1.0 - dst, 1.0 - np.clip(src, 0, 1))


def multiply_dark(frame, alpha, x, y, color=(0, 0, 0), opacity=1.0):
    """Darken the frame by a shadow alpha (towards `color`)."""
    h, w = alpha.shape[:2]
    r = roi(frame.shape, x, y, w, h)
    if r is None:
        return
    fx0, fy0, fx1, fy1, sx, sy = r
    a = np.ascontiguousarray(alpha[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0])
    if opacity != 1.0:
        a = a * opacity
    dst = frame[fy0:fy1, fx0:fx1]
    c = np.array(color, np.float32) / 255.0
    if not c.any():
        dst[:] = cv2.multiply(dst, _a3(1.0 - a))
    else:
        k = cv2.merge([1.0 - a * (1.0 - float(ci)) for ci in c])
        dst[:] = cv2.multiply(dst, k)


# ------------------------------------------------------------------ Deep Glow
GLOW_DEFAULT = {"radius": 26, "exposure": 0.85, "threshold": 0.0, "layers": 5, "falloff": 0.62,
                "color": None, "outer": None, "tint": 0.55, "opacity": 1.0}


def glow_params(g, base_color):
    p = dict(GLOW_DEFAULT)
    if isinstance(g, dict):
        p.update(g)
    p["inner_rgb"] = np.array(hex_rgb(p["color"], base_color), np.float32) / 255
    p["outer_rgb"] = np.array(hex_rgb(p["outer"], tuple(p["inner_rgb"] * 255)), np.float32) / 255
    return p


def glow(layer, p):
    """layer: premult RGBA canvas. Returns additive RGB (to be screen-composited).
    Computed at 1/4 resolution (the glow is soft) and upsampled."""
    h, w = layer.shape[:2]
    k = 4 if min(h, w) >= 64 else 1
    src = cv2.resize(layer[..., :3], (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA) if k > 1 \
        else layer[..., :3].copy()
    if p["threshold"] > 0:
        lum = src.max(2, keepdims=True)
        src = src * np.clip((lum - p["threshold"]) / max(1e-3, 1 - p["threshold"]), 0, 1)
    acc = np.zeros_like(src)
    n = int(p["layers"])
    wsum = 0.0
    for i in range(n):
        r = p["radius"] * (2 ** i) / 2 ** (n / 2 - 1) / k
        wgt = p["falloff"] ** i
        t = i / max(1, n - 1)
        tint = (p["inner_rgb"] * (1 - t) + p["outer_rgb"] * t).astype(np.float32)
        b = cv2.GaussianBlur(src, (0, 0), max(r, 0.5))
        lum = b.max(2, keepdims=True)
        acc += (b * (1 - p["tint"]) + lum * tint * p["tint"]) * wgt
        wsum += wgt
    acc *= p["exposure"] * 2.2 / wsum
    acc = 1 - np.exp(-acc)          # soft tonemap: bright but never clipped
    return cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR) if k > 1 else acc


# ------------------------------------------------------------------ Shadow Studio
SHADOW_DEFAULT = {"angle": 90, "long": 0.22, "contact": 0.045, "softness": 1.0, "strength": 0.55,
                  "steps": 10, "color": "#000000"}


def shadow_params(s, size):
    p = dict(SHADOW_DEFAULT)
    if isinstance(s, dict):
        p.update(s)
    p["size"] = size
    return p


def shadow(alpha, p):
    """alpha (h,w) of the element -> shadow alpha (same canvas). Element canvas must have padding.
    Computed at 1/2 resolution (shadows are soft)."""
    size = p["size"] / 2.0
    a = math.radians(p["angle"])
    dx, dy = math.cos(a), math.sin(a)
    H0, W0 = alpha.shape
    al = cv2.resize(alpha, (max(1, W0 // 2), max(1, H0 // 2)), interpolation=cv2.INTER_AREA)
    h, w = al.shape
    L = p["long"] * size
    acc = np.zeros_like(al)
    steps = max(2, int(p["steps"]))
    for i in range(1, steps + 1):          # extrusion with gradient falloff (Shadow Studio 'Gradient')
        t = i / steps
        M = np.float32([[1, 0, dx * L * t], [0, 1, dy * L * t]])
        np.maximum(acc, cv2.warpAffine(al, M, (w, h)) * ((1 - t) ** 1.3), out=acc)
    long_sh = cv2.GaussianBlur(acc, (0, 0), max(0.5, 0.10 * size * p["softness"])) * 0.65
    C = p["contact"] * size
    M = np.float32([[1, 0, dx * C], [0, 1, dy * C]])
    contact = cv2.GaussianBlur(cv2.warpAffine(al, M, (w, h)), (0, 0), max(0.5, 0.035 * size * p["softness"])) * 0.8
    out = np.clip(np.maximum(long_sh, contact) * p["strength"], 0, 1)
    return cv2.resize(out, (W0, H0), interpolation=cv2.INTER_LINEAR)


# ------------------------------------------------------------------ Liquid Glass
GLASS_DEFAULT = {"pad": [46, 30], "radius": 38, "blur": 20, "refraction": 1.0, "edge": 22,
                 "tint": "#10141A", "tint_opacity": 0.24, "brightness": 0.03, "light_angle": -55,
                 "highlight": 0.9, "chroma": 0.6, "shadow": 0.38, "shadow_dist": 18, "shadow_soft": 34,
                 "opacity": 1.0}


def glass_params(g):
    p = dict(GLASS_DEFAULT)
    if isinstance(g, dict):
        p.update(g)
    return p


def rrect_sdf(w, h, cx, cy, hw, hh, r, rot_deg=0.0, scale=1.0):
    """Signed distance (px, negative inside) of a rotated rounded rect on a w*h grid."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    x, y = xs - cx, ys - cy
    if rot_deg:
        c, s = math.cos(math.radians(-rot_deg)), math.sin(math.radians(-rot_deg))
        x, y = x * c - y * s, x * s + y * c
    x, y = x / scale, y / scale
    r = min(r, hw, hh)
    qx, qy = np.abs(x) - (hw - r), np.abs(y) - (hh - r)
    outside = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0)
    return (outside + inside - r) * scale


_GEO_CACHE = {}


def _glass_geometry(w, h, lcx, lcy, hw, hh, p, rot, scale):
    key = (w, h, round(lcx, 1), round(lcy, 1), round(hw, 1), round(hh, 1), p["radius"], round(rot, 2),
           round(scale, 3), p["edge"], p["refraction"], p["chroma"], p["light_angle"], p["shadow_dist"],
           p["shadow_soft"], p["highlight"])
    g = _GEO_CACHE.get(key)
    if g is not None:
        return g
    d = rrect_sdf(w, h, lcx, lcy, hw, hh, p["radius"], rot, scale)
    mask = np.clip(0.5 - d / 1.2, 0, 1)
    sh = None
    if p["shadow"] > 0:
        sd = rrect_sdf(w, h, lcx, lcy + p["shadow_dist"] * scale, hw, hh, p["radius"], rot, scale)
        sh = blur(np.clip(0.5 - sd, 0, 1), p["shadow_soft"] * scale) * (1 - mask)
    edge = max(4.0, p["edge"] * scale)
    gx = cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3) / 8
    nrm = np.sqrt(gx * gx + gy * gy) + 1e-6
    nx, ny = gx / nrm, gy / nrm
    rim = np.clip(1 + d / edge, 0, 1) ** 2
    disp = rim * edge * 1.4 * p["refraction"]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    maps = []
    for k in (1 + 0.06 * p["chroma"], 1.0, 1 - 0.06 * p["chroma"]):
        maps.append(((xs + nx * disp * k).astype(np.float32), (ys + ny * disp * k).astype(np.float32)))
    la = math.radians(p["light_angle"])
    lx, ly = math.cos(la), math.sin(la)
    facing = np.clip(nx * lx + ny * ly, 0, 1)
    band = np.clip(1 + d / (edge * 0.55), 0, 1) ** 3
    spec = band * (0.25 + facing ** 2 * 1.2) * p["highlight"]
    line = np.exp(-((d + 1.6) / 1.1) ** 2) * (0.35 + 0.65 * facing)
    back = np.clip(-(nx * lx + ny * ly), 0, 1) * band * 0.25 * p["highlight"]
    light = (spec + line * 0.55 + back * 0.6)[..., None].astype(np.float32)
    g = (mask[..., None].astype(np.float32), sh, maps, light)
    if len(_GEO_CACHE) > 64:
        _GEO_CACHE.clear()
    _GEO_CACHE[key] = g
    return g


def glass(frame, cx, cy, hw, hh, p, scale=1.0, rot=0.0, opacity=1.0):
    """Draw a liquid-glass plate centred at (cx,cy) with half size (hw,hh) onto frame (in place)."""
    if scale <= 0.01 or opacity <= 0.005:
        return
    ext = math.hypot(hw, hh) if rot else max(hw, hh)
    mx = int((ext if rot else hw) * scale + p["shadow_soft"] * 2 + 8)
    my = int((ext if rot else hh) * scale + p["shadow_dist"] + p["shadow_soft"] * 2 + 8)
    x0, y0 = int(cx - mx), int(cy - my)
    r = roi(frame.shape, x0, y0, 2 * mx, 2 * my)
    if r is None:
        return
    fx0, fy0, fx1, fy1, _, _ = r
    bg = frame[fy0:fy1, fx0:fx1]
    h, w = bg.shape[:2]
    mask, sh, maps, light = _glass_geometry(w, h, cx - fx0, cy - fy0, hw, hh, p, rot, scale)
    if sh is not None:
        bg *= (1 - sh[..., None] * (p["shadow"] * opacity))
    frosted = blur(bg, p["blur"] * max(scale, 0.3))
    out = np.empty_like(bg)
    for c in range(3):
        out[..., c] = cv2.remap(np.ascontiguousarray(frosted[..., c]), maps[c][0], maps[c][1], cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
    tint = np.array(hex_rgb(p["tint"]), np.float32) / 255
    out = out * (1 - p["tint_opacity"]) + tint * p["tint_opacity"] + p["brightness"] + light
    np.clip(out, 0, 1, out=out)
    a = mask * (opacity * p["opacity"])
    bg *= (1 - a)
    bg += out * a


# ------------------------------------------------------------------ motion-graphics background
class BgElement:
    """Full-frame animated background ('type': 'bg') for explainer moments, black gaps, B-roll holes.

    {"type": "bg", "start", "end", "colors": ["#0B1020", "#1B2550"], "blobs": ["#2F6BFF", "#00D1B2"],
     "grid": true, "fade": 0.25, "opacity": 1.0}
    """

    def __init__(self, el):
        self.el = el
        c = el.get("colors") or ["#0B1020", "#1A2148"]
        self.c1 = np.array(hex_rgb(c[0]), np.float32) / 255
        self.c2 = np.array(hex_rgb(c[-1]), np.float32) / 255
        self.blobs = [np.array(hex_rgb(b), np.float32) / 255 for b in (el.get("blobs") or ["#2F6BFF", "#00D1B2"])]
        self._base = None

    def extents(self):
        return None

    def draw(self, frame, t):
        e = self.el
        if t < e["start"] or t >= e["end"]:
            return
        H, W = frame.shape[:2]
        h, w = H // 4, W // 4
        if self._base is None:
            ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
            self._base = (self.c1 * (1 - ys) + self.c2 * ys) * np.ones((1, w, 1), np.float32)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            self._yy, self._xx = yy, xx
        img = self._base.copy()
        tt = t - e["start"]
        for i, col in enumerate(self.blobs):          # slowly drifting light blobs
            cx = w * (0.5 + 0.32 * math.sin(0.35 * tt + i * 2.1))
            cy = h * (0.45 + 0.28 * math.cos(0.27 * tt + i * 1.3))
            r2 = ((self._xx - cx) / (w * 0.45)) ** 2 + ((self._yy - cy) / (h * 0.3)) ** 2
            img += np.exp(-r2 * 1.6)[..., None] * col * 0.55
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)
        if e.get("grid", True):                       # faint tech grid, slowly scrolling
            step = 90
            off = int(tt * 18) % step
            g = np.zeros((H, W), np.float32)
            g[(np.arange(H) + off) % step == 0, :] = 1
            g[:, np.arange(W) % step == 0] = 1
            img += cv2.GaussianBlur(g, (0, 0), 0.8)[..., None] * 0.05
        img = 1 - np.exp(-img * 1.2)                  # soft tonemap
        fd = float(e.get("fade", 0.25))
        a = float(e.get("opacity", 1.0)) * min(1.0, tt / fd if fd else 1, (e["end"] - t) / fd if fd else 1)
        frame *= (1 - a)
        frame += img.astype(np.float32) * a
