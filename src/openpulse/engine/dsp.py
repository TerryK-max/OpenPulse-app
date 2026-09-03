"""Small numpy DSP primitives for the effect engine.

Blocks are short (a few ms). State (oscillator phase, filter memory, envelope
position) is carried across blocks so the output is continuous.
"""

from __future__ import annotations

import math

import numpy as np

TWO_PI = 2.0 * math.pi


def one_pole_alpha(fc_hz: float, dt: float) -> float:
    if fc_hz <= 0 or dt <= 0:
        return 1.0
    x = TWO_PI * fc_hz * dt
    return x / (x + 1.0)


def clamp01(x: np.ndarray | float):
    return np.clip(x, 0.0, 1.0)


def smoothstep(lo: float, hi: float, x: float) -> float:
    if hi <= lo:
        return 0.0 if x < lo else 1.0
    t = min(1.0, max(0.0, (x - lo) / (hi - lo)))
    return t * t * (3.0 - 2.0 * t)


class Smoother:
    """One-pole low-pass on a scalar parameter, stepped once per block."""

    def __init__(self, fc_hz: float, init: float = 0.0):
        self.fc = fc_hz
        self.y = init

    def step(self, target: float, dt: float) -> float:
        self.y += one_pole_alpha(self.fc, dt) * (target - self.y)
        return self.y

    def reset(self, value: float = 0.0) -> None:
        self.y = value


class Osc:
    """Phase-continuous oscillator. ``block`` returns [-1, 1]."""

    def __init__(self, phase: float = 0.0):
        self.phase = phase % 1.0

    def block(self, n: int, fs: int, freq_hz: float, shape: str = "sin", duty: float = 0.5) -> np.ndarray:
        inc = max(0.0, freq_hz) / fs
        ph = (self.phase + inc * np.arange(1, n + 1)) % 1.0
        self.phase = float(ph[-1]) if n else self.phase
        if shape == "sin":
            return np.sin(TWO_PI * ph)
        if shape == "tri":
            return 2.0 * np.abs(2.0 * (ph - 0.5)) - 1.0
        if shape == "saw":
            return 2.0 * ph - 1.0
        if shape == "square":
            return np.where(ph < duty, 1.0, -1.0)
        raise ValueError(f"unknown osc shape {shape!r}")

    def reset(self) -> None:
        self.phase = 0.0


class BandNoise:
    """White noise band-passed by two cascaded one-poles. Output ~[-1, 1]."""

    def __init__(self, rng: np.random.Generator, gain: float = 4.0):
        self.rng = rng
        self.gain = gain
        self._hp = 0.0  # low-pass at hi_hz
        self._lp = 0.0  # low-pass at lo_hz (subtracted -> band)

    def block(self, n: int, fs: int, lo_hz: float, hi_hz: float) -> np.ndarray:
        if n == 0:
            return np.zeros(0, np.float32)
        w = self.rng.standard_normal(n)
        a_hi = one_pole_alpha(hi_hz, 1.0 / fs)
        a_lo = one_pole_alpha(lo_hz, 1.0 / fs)
        out = np.empty(n, np.float64)
        hp, lp = self._hp, self._lp
        for i in range(n):
            hp += a_hi * (w[i] - hp)
            lp += a_lo * (hp - lp)
            out[i] = hp - lp
        self._hp, self._lp = hp, lp
        return np.clip(out * self.gain, -1.0, 1.0)

    def reset(self) -> None:
        self._hp = self._lp = 0.0


class OneShot:
    """Triggered attack/hold/decay envelope. ``block`` returns [0, 1]."""

    def __init__(self) -> None:
        self._t: float | None = None
        self._a = self._h = self._d = 0.0
        self._scale = 1.0

    def trigger(self, attack_ms: float, hold_ms: float, decay_ms: float, scale: float = 1.0) -> None:
        self._t = 0.0
        self._a = max(1e-4, attack_ms / 1000.0)
        self._h = max(0.0, hold_ms / 1000.0)
        self._d = max(1e-4, decay_ms / 1000.0)
        self._scale = scale

    @property
    def active(self) -> bool:
        return self._t is not None

    def block(self, n: int, fs: int) -> np.ndarray:
        if self._t is None or n == 0:
            return np.zeros(n, np.float32)
        dt = 1.0 / fs
        ts = self._t + dt * np.arange(n)
        end = self._a + self._h + self._d
        env = np.where(
            ts < self._a,
            ts / self._a,
            np.where(
                ts < self._a + self._h,
                1.0,
                np.where(ts < end, 1.0 - (ts - self._a - self._h) / self._d, 0.0),
            ),
        )
        self._t = float(self._t + n * dt)
        if self._t >= end:
            self._t = None
        return np.clip(env * self._scale, 0.0, None).astype(np.float32)

    def reset(self) -> None:
        self._t = None


class Gate:
    """Hysteretic noise gate. ``process`` returns a smoothed 0..1 gain."""

    def __init__(self, open_thr: float, close_thr: float, hold_s: float, slew_hz: float = 40.0):
        self.open_thr = open_thr
        self.close_thr = min(close_thr, open_thr)
        self.hold_s = hold_s
        self.slew_hz = slew_hz
        self._open = False
        self._since = 1e9
        self._g = 0.0

    def process(self, level: float, dt: float) -> float:
        if level >= self.open_thr:
            self._open = True
            self._since = 0.0
        elif self._open and level < self.close_thr:
            self._since += dt
            if self._since >= self.hold_s:
                self._open = False
        target = 1.0 if self._open else 0.0
        self._g += one_pole_alpha(self.slew_hz, dt) * (target - self._g)
        return self._g

    def reset(self) -> None:
        self._open = False
        self._g = 0.0
        self._since = 1e9


def soft_limit(x: np.ndarray, knee: float = 0.7) -> np.ndarray:
    """Soft knee toward 1.0. Linear below ``knee``, compressed above, asymptotes <1."""
    k = max(1e-3, knee)
    over = np.maximum(0.0, np.abs(x) - k)
    comp = k + (1.0 - k) * np.tanh(over / (1.0 - k))
    return np.sign(x) * np.where(np.abs(x) <= k, np.abs(x), comp)


class LeakyEnergy:
    """Leaky integrator of x^2 — a crude actuator-heat proxy."""

    def __init__(self, tau_s: float):
        self.tau = max(1e-3, tau_s)
        self.e = 0.0

    def update(self, block: np.ndarray, dt: float) -> float:
        ms = float(np.mean(block * block)) if block.size else 0.0
        self.e += (ms - self.e) * (1.0 - math.exp(-dt / self.tau))
        return self.e

    def reset(self) -> None:
        self.e = 0.0
