"""Shared constants and math for the aushev-reels skill.

Everything that mirrors the AUSHEV After Effects script (v1.7.0) lives here:
the spring expression, the 15-frame Bounce window, IN/OUT time mirroring.
"""
import json
import math
import os
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL_DIR, "assets")
SOUNDS_DIR = os.path.join(ASSETS, "sounds")
FONTS_DIR = os.path.join(ASSETS, "fonts")

W, H, FPS = 1080, 1920, 30
OVERSCAN = 1.25          # prepared source = 1.25x output -> headroom for zooms / eye-line shifts

# Instagram Reels safe zone for 1080x1920
SAFE = {"x0": 48, "x1": 1032, "y0": 225, "y1": 1552, "y_right_cut": 1097, "x1_low": 948}

# --- AUSHEV constants (from aushev.ae_v1.jsx) ---
BOUNCE_FRAMES = 15        # length of the bounce "splash" window
BOUNCE_ROT = 28.0         # degrees of extra rotation
SPRING_AMP, SPRING_FREQ, SPRING_DECAY = 0.06, 1.8, 5.0
# Word_*_Bounce preset: amount = 150*cos(2.5*t*2pi)/exp(7t), delay = Speed(0.1 s)*textIndex
WB_AMP, WB_FREQ, WB_DECAY, WB_DELAY = 1.5, 2.5, 7.0, 0.1

FONT_FILES = {
    "black": "InterDisplay-Black.otf",
    "extrabold": "InterDisplay-ExtraBold.otf",
    "bold": "InterDisplay-ExtraBold.otf",
    "semibold": "InterDisplay-SemiBold.otf",
    "medium": "InterDisplay-SemiBold.otf",
    "regular": "InterDisplay-SemiBold.otf",
}


def font_path(weight="extrabold"):
    return os.path.join(FONTS_DIR, FONT_FILES.get(str(weight).lower(), FONT_FILES["extrabold"]))


# ---------------------------------------------------------------- easing
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def ease_out_cubic(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_in_cubic(x):
    x = clamp(x)
    return x ** 3


def ease_in_out_cubic(x):
    x = clamp(x)
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_expo(x):
    x = clamp(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def ease_in_expo(x):
    x = clamp(x)
    return 0.0 if x <= 0 else 2 ** (10 * x - 10)


def smoothstep(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


# AE range selector with "Ease High -50" on a Ramp-Up shape feels like a soft ease-out.
EASES = {
    "linear": clamp, "out": ease_out_cubic, "in": ease_in_cubic, "inout": ease_in_out_cubic,
    "expo": ease_out_expo, "expo_in": ease_in_expo, "smooth": smoothstep,
}


def spring_tail(v, dt, amp=SPRING_AMP, freq=SPRING_FREQ, decay=SPRING_DECAY):
    """AUSHEV spring: value + v*amp*sin(freq*t*2pi)/exp(decay*t) for t>=0 after a key."""
    if dt < 0:
        return 0.0
    return v * amp * math.sin(freq * dt * 2 * math.pi) / math.exp(decay * dt)


def keyed_value(keys, t):
    """keys: [(t, v), ...] linear interpolation, holds outside."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1e-9)
    return keys[-1][1]


def bounce_state(t, start, end, io="in", base_scale=1.0, base_rot=0.0, frames=BOUNCE_FRAMES):
    """Replicates applyBounceToLayer + bounceExpression.

    IN : Scale 0 -> base, Rotation base+28 -> base over a 15-frame window at the in-point,
         then the decaying spring after the key.
    OUT: Scale base -> 0, Rotation base -> base+28 ending at the out-point, with the mirrored
         'anticipation' wobble before the first OUT key.
    The window never exceeds half the element duration, so IN and OUT never overlap.
    Returns (scale, rotation_deg).
    """
    dur = max(end - start, 1e-6)
    D = min(frames / FPS, dur / 2)
    in_on = io in ("in", "both", True)
    out_on = io in ("out", "both")
    s_keys, r_keys = [], []
    if in_on:
        s_keys += [(start, 0.0), (start + D, base_scale)]
        r_keys += [(start, base_rot + BOUNCE_ROT), (start + D, base_rot)]
    if out_on:
        s_keys += [(end - D, base_scale), (end, 0.0)]
        r_keys += [(end - D, base_rot), (end, base_rot + BOUNCE_ROT)]
    if not s_keys:
        return base_scale, base_rot
    split = (start + end) / 2 if (in_on and out_on) else None
    out = []
    for keys in (s_keys, r_keys):
        val = keyed_value(keys, t)
        # previous key p (time <= t) and next key q (time > t)
        p = max([i for i, k in enumerate(keys) if k[0] <= t], default=None)
        q = min([i for i, k in enumerate(keys) if k[0] > t], default=None)
        if in_on and p is not None and p >= 1 and (split is None or keys[p][0] < split):
            k0, k1 = keys[p - 1], keys[p]
            v1 = (k1[1] - k0[1]) / max(k1[0] - k0[0], 1e-9)   # velocity just before key p
            val += spring_tail(v1, t - k1[0])
        if out_on and q is not None and q + 1 < len(keys) and (split is None or keys[q][0] >= split):
            k0, k1 = keys[q], keys[q + 1]
            v2 = (k1[1] - k0[1]) / max(k1[0] - k0[0], 1e-9)   # velocity just after key q
            val -= spring_tail(v2, k0[0] - t)
        out.append(val)
    return out[0], out[1]


# ---------------------------------------------------------------- misc helpers
def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, obj):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def hex_rgb(c, default=(255, 255, 255)):
    if c is None:
        return default
    if isinstance(c, (list, tuple)):
        return tuple(int(v) for v in c[:3])
    c = str(c).lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


class Budget:
    """Cloud sandbox commands live ~300 s. Heavy steps stop at the budget and resume next call."""

    def __init__(self, seconds):
        self.t0 = time.time()
        self.seconds = seconds

    def left(self):
        return self.seconds - (time.time() - self.t0)

    def ok(self, need=0.0):
        return self.left() > need


def work_paths(work):
    return {
        "work": work,
        "src": os.path.join(work, "src.mp4"),
        "voice": os.path.join(work, "voice.wav"),
        "meta": os.path.join(work, "meta.json"),
        "words": os.path.join(work, "words.json"),
        "track": os.path.join(work, "track.json"),
        "chunks": os.path.join(work, "chunks"),
        "preview": os.path.join(work, "preview"),
        "sfx_wav": os.path.join(work, "sfx_mix.wav"),
    }
