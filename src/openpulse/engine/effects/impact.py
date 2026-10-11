"""impact — a big transient hit (contact, hard landing, wall)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import OneShot
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class ImpactEffect(Effect):
    id = "impact"
    kind = "event"

    def reset(self) -> None:
        self._env = OneShot()
        self._prev_impact = 0.0
        self._prev_damage = 0.0
        self._cool_until = -1e9

    test_duration_s = 1.0

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        g = self.p.ref_g if t0 <= 0.0 < t1 else 0.0
        return replace(f, on_track=True, paused=False, impact_g=g)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        active_frame = f.on_track and not f.paused

        dmg = sum(f.damage) if f.damage else 0.0
        d_dmg = max(0.0, dmg - self._prev_damage)
        self._prev_damage = dmg

        rising = f.impact_g > self._prev_impact + 1e-6
        self._prev_impact = f.impact_g

        trig_mag = 0.0
        if active_frame and ctx.t >= self._cool_until:
            if rising and f.impact_g >= p.trigger_g:
                trig_mag = f.impact_g
            elif d_dmg >= p.damage_trigger:
                trig_mag = max(f.impact_g, p.trigger_g)
        if trig_mag > 0.0:
            scale = min(1.0, trig_mag / max(1e-3, p.ref_g)) * p.level * p.gain
            self._env.trigger(p.attack_ms, p.hold_ms, p.decay_ms, scale)
            self._cool_until = ctx.t + p.cooldown_ms / 1000.0

        env = self._env.block(ctx.n, ctx.fs)
        if not env.any():
            return EffectBlock(signal=np.zeros(ctx.n, np.float32))
        return EffectBlock(signal=env, duck=p.duck_db if self._env.active else 0.0, level=float(np.max(env)))
