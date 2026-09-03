"""engine — a low throb that speeds up and swells with revs and load."""

from __future__ import annotations

import numpy as np

from openpulse.engine.dsp import Osc, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class EngineRpmEffect(Effect):
    id = "engine"
    kind = "bed"
    duckable = True

    def reset(self) -> None:
        self._osc = Osc()
        self._freq = Smoother(8.0, 6.0)
        self._level = Smoother(10.0)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if not (f.on_track and not f.paused) or f.speed_ms < 0.5:
            lvl = self._level.step(0.0, ctx.dt)
            return EffectBlock(signal=np.zeros(ctx.n, np.float32), level=lvl)

        rpm_norm = 0.0
        if f.rpm_max > 0:
            rpm_norm = max(0.0, min(1.2, f.rpm / f.rpm_max))
        freq = self._freq.step(p.freq_lo_hz + rpm_norm * (p.freq_hi_hz - p.freq_lo_hz), ctx.dt)
        target = min(p.ceiling, (p.floor + f.throttle * p.throttle_gain + rpm_norm * p.rpm_gain) * p.gain)
        lvl = self._level.step(target, ctx.dt)

        wave = 0.5 + 0.5 * self._osc.block(ctx.n, ctx.fs, freq, p.shape)
        return EffectBlock(signal=(wave * lvl).astype(np.float32), level=lvl)
