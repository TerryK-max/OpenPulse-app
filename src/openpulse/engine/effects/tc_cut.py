"""tc_cut — a light stutter while traction control is intervening."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import Osc, Smoother
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class TcCutEffect(Effect):
    id = "tc_cut"
    kind = "event"

    def reset(self) -> None:
        self._osc = Osc()
        self._env = Smoother(50.0)

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        return replace(f, on_track=True, paused=False, tc_active=True,
                       throttle=max(f.throttle, 1.0, self.p.throttle_min))

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        on = f.on_track and not f.paused and f.tc_active and f.throttle >= p.throttle_min
        self._env.fc = 70.0 if on else 30.0
        lvl = self._env.step(p.level * p.gain if on else 0.0, ctx.dt)
        if lvl < 1e-3:
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        wave = 0.5 + 0.5 * self._osc.block(ctx.n, ctx.fs, p.freq_hz, "square", p.duty)
        sig = (wave * lvl).astype(np.float32)
        return EffectBlock(signal=sig, duck=p.duck_db if on else 0.0, level=lvl)
