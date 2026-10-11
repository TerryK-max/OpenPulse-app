"""rev_limiter — the hard stutter of the engine bouncing off the limiter."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import Osc, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class RevLimiterEffect(Effect):
    id = "rev_limiter"
    kind = "event"

    def reset(self) -> None:
        self._osc = Osc()
        self._env = Smoother(120.0)

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        return replace(f, on_track=True, paused=False, rev_limiter=True)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        on = f.on_track and not f.paused and f.rev_limiter
        self._env.fc = 200.0 if on else 60.0
        lvl = self._env.step(p.level * p.gain if on else 0.0, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        wave = np.where(self._osc.block(ctx.n, ctx.fs, p.freq_hz, "square", p.duty) > 0, 1.0, 0.0)
        sig = (wave * lvl).astype(np.float32)
        return EffectBlock(signal=sig, duck=p.duck_db if on else 0.0, level=lvl)
