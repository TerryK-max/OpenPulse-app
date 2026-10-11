"""offtrack — coarse rumble with wheels off the racing surface (grass / gravel)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import BandNoise, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext
from openpulse.engine.effects._common import speed_curve
from openpulse.telemetry.frame import Cap


class OfftrackEffect(Effect):
    id = "offtrack"
    kind = "event"

    def reset(self) -> None:
        self._noise: BandNoise | None = None
        self._level = Smoother(25.0)

    def _fraction(self, f) -> float:  # noqa: ANN001
        if Cap.OFF_TRACK_COUNT in f.caps:
            return f.off_track_wheels / 4.0
        if self.p.use_tyre_dirt:
            dirt = [d for d in f.tyre_dirt if math.isfinite(d)]
            if dirt:
                return sum(1 for d in dirt if d > self.p.dirt_thr) / 4.0
        return 0.0

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        return replace(f, on_track=True, paused=False, caps=f.caps | Cap.OFF_TRACK_COUNT,
                       off_track_wheels=4, speed_ms=max(f.speed_ms, self.p.speed_ref_ms))

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if self._noise is None:
            self._noise = BandNoise(ctx.rng)
        active = f.on_track and not f.paused
        frac = self._fraction(f) if active else 0.0
        spd = speed_curve(f.speed_ms, p.speed_min_ms, p.speed_ref_ms, 1.0)
        target = frac * spd * p.level * p.gain
        lvl = self._level.step(target, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        tex = 0.5 + 0.5 * self._noise.block(ctx.n, ctx.fs, p.noise_lo_hz, p.noise_hi_hz)
        sig = (tex * lvl).astype(np.float32)
        return EffectBlock(signal=sig, duck=2.0 if lvl > 0.2 else 0.0, level=lvl)
