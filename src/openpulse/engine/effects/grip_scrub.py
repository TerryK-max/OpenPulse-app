"""grip_scrub — fine granular sizzle that swells as the tyres approach the
friction limit (squealing, not yet a full lockup or slide).

Input is the Deriver's ``grip_scrub_front`` / ``grip_scrub_rear`` (0..~1.5); this
effect adds its own input remap (``in_lo``/``in_hi``/``curve``) so the response
can be shaped independently of the Deriver threshold.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from openpulse.engine.dsp import BandNoise, Osc, Smoother, smoothstep
from openpulse.engine.effect import Effect, EffectBlock, EffectContext


class GripScrubEffect(Effect):
    id = "grip_scrub"
    kind = "bed"

    def reset(self) -> None:
        self._noise: BandNoise | None = None
        self._am = Osc()
        self._level = Smoother(9.0)

    def _shaped(self, raw: float) -> float:
        if not math.isfinite(raw):
            return 0.0
        return smoothstep(self.p.in_lo, self.p.in_hi, raw) ** self.p.curve

    test_duration_s = 4.0

    def test_frame(self, f, t0, t1):  # noqa: ANN001
        # triangle 0 -> 1 -> 0: lets you feel the whole in_lo..in_hi / curve slope
        x = t0 / self.test_duration_s
        tri = 1.0 - abs(2.0 * min(1.0, x) - 1.0)
        return replace(f, on_track=True, paused=False,
                       grip_scrub=tri, grip_scrub_front=tri, grip_scrub_rear=tri)

    def render(self, ctx: EffectContext) -> EffectBlock:
        f = ctx.frame
        p = self.p
        if self._noise is None:
            self._noise = BandNoise(ctx.rng)

        fr = self._shaped(f.grip_scrub_front) * p.front_mix
        re = self._shaped(f.grip_scrub_rear) * p.rear_mix
        raw = max(fr, re)
        active = f.on_track and not f.paused
        target = min(p.ceiling, raw * p.gain) if active else 0.0
        lvl = self._level.step(target, ctx.dt)

        tex = 0.5 + 0.5 * self._noise.block(ctx.n, ctx.fs, p.noise_lo_hz, p.noise_hi_hz)
        am = 1.0 - p.am_depth * (0.5 + 0.5 * self._am.block(ctx.n, ctx.fs, p.am_hz, "sin"))
        sig = (tex * am * lvl).astype(np.float32)
        # as the tyres load up, the ambient road bed steps aside for the scrub
        duck = p.duck_db * (lvl / max(1e-3, p.ceiling)) if lvl > 0.05 else 0.0
        return EffectBlock(signal=sig, duck=duck, level=lvl, meta={"front": fr, "rear": re})
