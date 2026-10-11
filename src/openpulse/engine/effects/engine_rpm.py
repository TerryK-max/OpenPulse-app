"""engine — a low throb that speeds up and swells with revs and load."""

from __future__ import annotations

from dataclasses import replace

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

    test_duration_s = 2.5

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        # sweep idle -> redline so both the throb frequency and its swell are felt
        sweep = min(1.0, t0 / 2.0)
        return replace(f, on_track=True, paused=False, rpm_max=8000.0,
                       rpm=8000.0 * (0.25 + 0.75 * sweep), throttle=0.8)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if f.paused or f.rpm <= 0:
            lvl = self._level.step(0.0, ctx.dt)
            return EffectBlock(signal=np.zeros(ctx.n, np.float32), level=lvl)

        rpm_norm = 0.0
        if f.rpm_max > 0:
            rpm_norm = max(0.0, min(1.2, f.rpm / f.rpm_max))
        elif f.rpm > 0:
            rpm_norm = max(0.0, min(1.2, f.rpm / 8000.0))
        freq = self._freq.step(p.freq_lo_hz + rpm_norm * (p.freq_hi_hz - p.freq_lo_hz), ctx.dt)
        target = min(p.ceiling, (p.floor + f.throttle * p.throttle_gain + rpm_norm * p.rpm_gain) * p.gain)
        lvl = self._level.step(target, ctx.dt)

        wave = 0.5 + 0.5 * self._osc.block(ctx.n, ctx.fs, freq, p.shape)
        return EffectBlock(signal=(wave * lvl).astype(np.float32), level=lvl)
