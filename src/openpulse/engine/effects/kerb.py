"""kerb — riding a kerb / rumble strip: one axle oscillating fast in the
suspension, plus a crunchy texture on top.
"""

from __future__ import annotations

import math

import numpy as np

from openpulse.engine.dsp import BandNoise, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext
from openpulse.telemetry.frame import Cap


class KerbEffect(Effect):
    id = "kerb"
    kind = "event"

    def reset(self) -> None:
        self._noise: BandNoise | None = None
        self._env = Smoother(60.0)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if self._noise is None:
            self._noise = BandNoise(ctx.rng)

        active = f.on_track and not f.paused
        # fastest-moving axle in the suspension
        fl = [abs(v) for v in f.susp_vel_ms[:2] if math.isfinite(v)]
        rl = [abs(v) for v in f.susp_vel_ms[2:] if math.isfinite(v)]
        axle = max((max(fl) if fl else 0.0), (max(rl) if rl else 0.0))
        inten = max(0.0, min(1.3, (axle - p.susp_vel_thr) / max(1e-3, p.susp_vel_range)))
        if f.off_track_wheels:
            inten = min(1.3, inten + p.off_track_boost * f.off_track_wheels / 4.0)
        if p.needs_abs_travel and Cap.SUSP_TRAVEL_ABS not in f.caps:
            inten *= 0.5  # ride-height proxy is noisier — be conservative

        target = (inten * p.level * p.gain) if active else 0.0
        self._env.fc = 1000.0 / max(0.5, p.attack_ms) if target > self._env.y \
            else 1000.0 / max(1.0, p.release_ms)
        lvl = self._env.step(target, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))

        tex = 0.5 + 0.5 * self._noise.block(ctx.n, ctx.fs, p.texture_hz * 0.5, p.texture_hz * 1.5)
        body = (1.0 - p.texture_depth) + p.texture_depth * tex
        sig = np.clip(body * lvl, 0.0, None).astype(np.float32)
        return EffectBlock(signal=sig, duck=p.duck_db if lvl > 0.15 else 0.0, level=float(np.max(sig)))
