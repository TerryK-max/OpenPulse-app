"""gearshift — a single sharp thump on each shift (up ≠ down)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import OneShot
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class GearshiftEffect(Effect):
    id = "gearshift"
    kind = "event"

    def reset(self) -> None:
        self._env = OneShot()

    test_duration_s = 1.2

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        change = 1 if t0 <= 0.0 < t1 else (-1 if t0 <= 0.6 < t1 else 0)
        return replace(f, on_track=True, paused=False, gear_change=change)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if f.gear_change and (f.on_track and not f.paused):
            lvl = p.level_up if f.gear_change > 0 else p.level_down
            self._env.trigger(p.attack_ms, p.hold_ms, p.decay_ms, lvl * p.gain)
        env = self._env.block(ctx.n, ctx.fs)
        if not env.any():
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        return EffectBlock(signal=env, duck=p.duck_db if self._env.active else 0.0, level=float(np.max(env)))
