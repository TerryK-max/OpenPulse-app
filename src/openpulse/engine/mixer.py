"""Mixer — combine effect blocks into one 0..1 amplitude envelope.

beds sum (capped) → events duck the beds → sum → master → soft limiter →
noise gate → thermal foldback → output gamma.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from openpulse.engine.dsp import Gate, LeakyEnergy, Smoother, soft_limit
from openpulse.engine.effect import EffectBlock
from openpulse.engine.params import MixerParams


@dataclass(slots=True)
class MixOutput:
    signal: np.ndarray                 # float32 [n], 0..1
    peak: float = 0.0
    duck_gain: float = 1.0
    gate_gain: float = 1.0
    thermal_gain: float = 1.0
    thermal_energy: float = 0.0
    effect_levels: dict[str, float] = field(default_factory=dict)


class Mixer:
    def __init__(self, params: MixerParams):
        self.p = params
        self.reset()

    def reset(self) -> None:
        self._duck = Smoother(8.0, 0.0)     # smoothed duck amount in dB
        self._gate = Gate(self.p.gate_open, self.p.gate_close, self.p.gate_hold_ms / 1000.0)
        self._thermal = LeakyEnergy(self.p.thermal_tau_s)

    def mix(
        self,
        blocks: dict[str, EffectBlock],
        dt: float,
        *,
        external_limit: float = 1.0,
        enable: float = 1.0,
        kinds: dict[str, str],
        duckable: dict[str, bool],
    ) -> MixOutput:
        p = self.p
        n = next((b.signal.size for b in blocks.values()), 0)
        ambient = np.zeros(n, np.float64)     # duckable beds (road, engine)
        foreground = np.zeros(n, np.float64)  # non-duckable beds (grip_scrub)
        events = np.zeros(n, np.float64)
        duck_req = 0.0
        for eid, b in blocks.items():
            if kinds.get(eid) == "bed":
                (ambient if duckable.get(eid) else foreground)[:] += b.signal
            else:
                events += b.signal
            duck_req = max(duck_req, b.duck)

        # duck: attack fast, release per config
        self._duck.fc = 30.0 if duck_req > self._duck.y else 1000.0 / max(1.0, p.duck_release_ms)
        duck_db = self._duck.step(duck_req, dt)
        duck_gain = 10.0 ** (-duck_db / 20.0)

        beds = np.minimum(ambient * duck_gain + foreground, p.bed_ceiling)
        raw = (beds + events) * p.master_gain * max(0.0, min(1.0, enable))

        limited = np.clip(soft_limit(raw, p.limiter_knee), 0.0, 1.0)

        gate_gain = self._gate.process(float(np.max(limited)) if n else 0.0, dt)
        gated = limited * gate_gain

        energy = self._thermal.update(gated, dt)
        thermal_gain = 1.0
        if energy > p.thermal_soft:
            span = max(1e-3, p.thermal_hard - p.thermal_soft)
            thermal_gain = max(0.0, 1.0 - (energy - p.thermal_soft) / span)
        thermal_gain = min(thermal_gain, max(0.0, external_limit))

        out = np.clip(gated * thermal_gain, 0.0, 1.0)
        if p.output_curve != 1.0:
            out = out ** p.output_curve

        return MixOutput(
            signal=out.astype(np.float32),
            peak=float(np.max(out)) if n else 0.0,
            duck_gain=duck_gain,
            gate_gain=gate_gain,
            thermal_gain=thermal_gain,
            thermal_energy=energy,
            effect_levels={eid: b.level for eid, b in blocks.items()},
        )

    # keep the gate/thermal in sync if the user edits params live
    def sync_params(self) -> None:
        self._gate.open_thr = self.p.gate_open
        self._gate.close_thr = min(self.p.gate_close, self.p.gate_open)
        self._gate.hold_s = self.p.gate_hold_ms / 1000.0
        self._thermal.tau = max(1e-3, self.p.thermal_tau_s)
