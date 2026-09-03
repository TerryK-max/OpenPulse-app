"""road — the continuous "the car is moving on tarmac" bed."""

from __future__ import annotations

import math

import numpy as np

from openpulse.engine.dsp import BandNoise, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext
from openpulse.engine.effects._common import finite, speed_curve


class RoadEffect(Effect):
    id = "road"
    kind = "bed"
    duckable = True

    def reset(self) -> None:
        self._noise: BandNoise | None = None
        self._level = Smoother(6.0)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if self._noise is None:
            self._noise = BandNoise(ctx.rng)

        base = speed_curve(f.speed_ms, p.speed_min_ms, p.speed_ref_ms, p.curve)
        rough = 0.0
        sv = finite([abs(v) for v in f.susp_vel_ms])
        if sv and p.susp_ref_ms > 0:
            rough = min(1.5, (sum(sv) / len(sv)) / p.susp_ref_ms) * p.susp_gain
        target = min(p.ceiling, (base + rough) * p.gain) if (f.on_track and not f.paused) else 0.0
        lvl = self._level.step(target, ctx.dt)

        tex = 0.5 + 0.5 * self._noise.block(ctx.n, ctx.fs, p.noise_lo_hz, p.noise_hi_hz)
        sig = (tex * lvl).astype(np.float32)
        return EffectBlock(signal=sig, level=lvl if math.isfinite(lvl) else 0.0)
