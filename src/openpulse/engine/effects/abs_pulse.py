"""abs_pulse — the regular pumping you feel through the pedal when ABS acts."""

from __future__ import annotations

import numpy as np

from openpulse.engine.dsp import Osc, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class AbsPulseEffect(Effect):
    id = "abs_pulse"
    kind = "event"

    def reset(self) -> None:
        self._osc = Osc()
        self._env = Smoother(50.0)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        on = f.on_track and not f.paused and f.abs_active and f.brake >= p.brake_min
        self._env.fc = 60.0 if on else 25.0
        lvl = self._env.step(p.level * p.gain if on else 0.0, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        wave = 0.5 + 0.5 * self._osc.block(ctx.n, ctx.fs, p.freq_hz, "square", p.duty)
        sig = (wave * lvl).astype(np.float32)
        return EffectBlock(signal=sig, duck=p.duck_db if on else 0.0, level=lvl)
