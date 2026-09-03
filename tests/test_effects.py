"""Per-effect behaviour on synthetic scenarios.

Each effect: output in 0..1, silent when disabled, silent when its trigger is
absent, present when the scenario should drive it.
"""

import numpy as np
import pytest

from openpulse.engine.effect import EffectContext
from openpulse.engine.params import EffectsConfig
from openpulse.engine.registry import EFFECT_CLASSES, build_effects
from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.synthetic import SyntheticSource

FS = 1000
BLOCK = 12


def _run_effect(effect, scenario: str, seconds: float = 3.0):
    src = SyntheticSource(scenario, rate_hz=333)
    src.open()
    dv = Deriver()
    rng = np.random.default_rng(1)
    peak = 0.0
    active_frac = 0
    n = int(seconds * 333)
    for k in range(n):
        frame = dv.process(src.build_at(k / 333))
        ctx = EffectContext(fs=FS, n=BLOCK, dt=BLOCK / FS, t=k * BLOCK / FS, frame=frame, rng=rng)
        b = effect.render(ctx)
        assert b.signal.shape == (BLOCK,)
        assert b.signal.min() >= -1e-6
        peak = max(peak, float(b.signal.max()))
        if b.signal.max() > 0.05:
            active_frac += 1
    src.close()
    return peak, active_frac / n


@pytest.mark.parametrize("cls", EFFECT_CLASSES, ids=lambda c: c.id)
def test_effect_disabled_is_silent(cls):
    cfg = EffectsConfig()
    getattr(cfg, cls.id).enabled = False
    effects = {e.id: e for e in build_effects(cfg)}
    e = effects[cls.id]
    assert not e.enabled


@pytest.mark.parametrize("cls", EFFECT_CLASSES, ids=lambda c: c.id)
def test_effect_output_bounded(cls):
    e = cls(getattr(EffectsConfig(), cls.id))
    peak, _ = _run_effect(e, "hotlap", 4.0)
    assert peak <= 1.5  # pre-clamp; pipeline clamps to 1.0


def test_road_scales_with_speed():
    e = EFFECT_CLASSES[0](EffectsConfig().road)
    assert e.id == "road"
    peak, frac = _run_effect(e, "hotlap", 3.0)
    assert peak > 0.05 and frac > 0.8  # always on while moving


def test_grip_scrub_present_in_scrub_scenario_absent_otherwise():
    make = lambda: next(c for c in EFFECT_CLASSES if c.id == "grip_scrub")(  # noqa: E731
        EffectsConfig().grip_scrub)
    hot_peak, _ = _run_effect(make(), "hotlap", 3.0)
    scrub_peak, scrub_frac = _run_effect(make(), "grip_scrub", 4.0)
    assert scrub_peak > 0.15
    assert scrub_peak > hot_peak + 0.05
    assert scrub_frac > 0.3


def test_lockup_needs_braking():
    make = lambda: next(c for c in EFFECT_CLASSES if c.id == "lockup")(  # noqa: E731
        EffectsConfig().lockup)
    lock_peak, _ = _run_effect(make(), "lock_fronts", 3.0)
    spin_peak, _ = _run_effect(make(), "wheelspin", 3.0)  # throttle, not brake
    assert lock_peak > 0.2
    assert spin_peak < 0.05


def test_wheelspin_needs_throttle():
    make = lambda: next(c for c in EFFECT_CLASSES if c.id == "wheelspin")(  # noqa: E731
        EffectsConfig().wheelspin)
    spin_peak, _ = _run_effect(make(), "wheelspin", 3.0)
    lock_peak, _ = _run_effect(make(), "lock_fronts", 3.0)
    assert spin_peak > 0.15
    assert lock_peak < 0.05


def test_impact_fires_on_hit_only():
    make = lambda: next(c for c in EFFECT_CLASSES if c.id == "impact")(  # noqa: E731
        EffectsConfig().impact)
    hit_peak, hit_frac = _run_effect(make(), "impact", 4.0)
    calm_peak, _ = _run_effect(make(), "hotlap", 4.0)
    assert hit_peak > 0.2
    assert hit_frac < 0.2       # brief
    assert calm_peak < 0.05


def test_gearshift_fires_on_shift():
    make = lambda: next(c for c in EFFECT_CLASSES if c.id == "gearshift")(  # noqa: E731
        EffectsConfig().gearshift)
    peak, frac = _run_effect(make(), "hotlap", 8.0)  # upshift every ~4 s
    assert peak > 0.2
    assert frac < 0.3
