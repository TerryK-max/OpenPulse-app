"""Shared logic for lockup / wheelspin: an intensity from wheel lock ratio on one
axle, driving a rough gated oscillator with a release tail.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import BandNoise, Osc, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext
from openpulse.engine.effects._common import axle_max, axle_min


class SlipEventEffect(Effect):
    kind = "event"
    _front = True          # lockup=True (front), wheelspin=False (rear)
    _direction = "under"   # "under" = ratio below thr (lock), "over" = above (spin)

    def reset(self) -> None:
        self._osc = Osc()
        self._noise: BandNoise | None = None
        self._env = Smoother(30.0)   # attack/release smoothing of the drive level

    def _intensity(self, ctx: EffectContext) -> float:
        f = ctx.frame
        p = self.p
        if self._direction == "under":
            if f.brake < p.brake_min:
                return 0.0
            r = axle_min(f.lock_ratio, self._front)
            if not math.isfinite(r):
                return 0.0
            return max(0.0, min(1.5, (p.lock_ratio_thr - r) / max(1e-3, p.lock_ratio_range)))
        else:
            if f.throttle < p.throttle_min:
                return 0.0
            r = axle_max(f.lock_ratio, self._front)
            if not math.isfinite(r):
                return 0.0
            return max(0.0, min(1.5, (r - p.lock_ratio_thr) / max(1e-3, p.lock_ratio_range)))

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        p = self.p
        if self._direction == "under":      # lockup: front wheels dragging under braking
            lock = p.lock_ratio_thr - p.lock_ratio_range
            ratios = (lock, lock, 1.0, 1.0) if self._front else (1.0, 1.0, lock, lock)
            return replace(f, on_track=True, paused=False, lock_ratio=ratios,
                           brake=max(f.brake, 1.0, p.brake_min))
        spin = p.lock_ratio_thr + p.lock_ratio_range    # wheelspin: rear over-rotating
        ratios = (1.0, 1.0, spin, spin) if not self._front else (spin, spin, 1.0, 1.0)
        return replace(f, on_track=True, paused=False, lock_ratio=ratios,
                       throttle=max(f.throttle, 1.0, p.throttle_min))

    def render(self, ctx: EffectContext) -> EffectBlock:
        p = self.p
        if self._noise is None:
            self._noise = BandNoise(ctx.rng)
        active = ctx.frame.on_track and not ctx.frame.paused
        target = (self._intensity(ctx) * p.level * p.gain) if active else 0.0

        # fast attack, slow release
        self._env.fc = 60.0 if target > self._env.y else (1000.0 / max(1.0, p.release_ms))
        lvl = self._env.step(target, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))

        osc = 0.5 + 0.5 * self._osc.block(ctx.n, ctx.fs, p.freq_hz, "tri")
        noise = 0.5 + 0.5 * self._noise.block(ctx.n, ctx.fs, p.freq_hz, p.freq_hz * 3.0)
        body = (1.0 - p.rough) * osc + p.rough * noise
        sig = np.clip(body * lvl, 0.0, None).astype(np.float32)
        return EffectBlock(signal=sig, duck=p.duck_db if lvl > 0.1 else 0.0, level=float(np.max(sig)))
