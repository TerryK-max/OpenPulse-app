"""Mixer — clamping, gate, ducking, thermal foldback."""

import numpy as np

from openpulse.engine.effect import EffectBlock
from openpulse.engine.mixer import Mixer
from openpulse.engine.params import MixerParams

N = 12


def _block(v: float, duck: float = 0.0) -> EffectBlock:
    return EffectBlock(signal=np.full(N, v, np.float32), duck=duck)


def _mix(m, blocks, *, dt=0.012, enable=1.0, external_limit=1.0):
    kinds = {k: ("bed" if k in ("road", "engine", "grip_scrub") else "event") for k in blocks}
    duckable = {k: k in ("road", "engine") for k in blocks}
    return m.mix(blocks, dt, enable=enable, external_limit=external_limit, kinds=kinds, duckable=duckable)


def test_output_never_exceeds_one():
    m = Mixer(MixerParams())
    out = _mix(m, {"road": _block(0.9), "lockup": _block(0.9), "kerb": _block(0.9)})
    assert out.signal.max() <= 1.0


def test_bed_ceiling_caps_ambient():
    p = MixerParams(bed_ceiling=0.3, limiter_knee=0.99, master_gain=1.0)
    m = Mixer(p)
    out = _mix(m, {"road": _block(0.6), "engine": _block(0.6)})
    assert out.signal.max() <= 0.3 + 1e-3


def test_gate_closes_on_silence():
    m = Mixer(MixerParams(gate_hold_ms=20))
    for _ in range(5):
        _mix(m, {"road": _block(0.4)})
    assert m._gate._g > 0.5
    for _ in range(60):
        out = _mix(m, {"road": _block(0.0)})
    assert out.gate_gain < 0.1
    assert out.signal.max() == 0.0


def test_ducking_reduces_ambient_bed():
    m = Mixer(MixerParams(limiter_knee=0.99))
    base = _mix(m, {"road": _block(0.4), "lockup": _block(0.0)})
    for _ in range(20):
        ducked = _mix(m, {"road": _block(0.4), "lockup": _block(0.0, duck=6.0)})
    assert ducked.duck_gain < 0.7
    assert ducked.signal.max() < base.signal.max()


def test_thermal_foldback_under_sustained_drive():
    p = MixerParams(thermal_tau_s=1.1, thermal_soft=0.05, thermal_hard=0.15, limiter_knee=0.99)
    m = Mixer(p)
    last = None
    for _ in range(8000):
        last = _mix(m, {"road": _block(0.5)}, dt=0.01)
    assert last.thermal_gain < 0.5
    assert last.thermal_energy > p.thermal_soft


def test_external_limit_cuts_output():
    m = Mixer(MixerParams())
    out = _mix(m, {"road": _block(0.5)}, external_limit=0.0)
    assert out.signal.max() == 0.0


def test_enable_ramp_scales_output():
    m = Mixer(MixerParams(limiter_knee=0.99))
    full = _mix(m, {"road": _block(0.4)}, enable=1.0)
    half = _mix(m, {"road": _block(0.4)}, enable=0.5)
    assert half.signal.max() < full.signal.max()
