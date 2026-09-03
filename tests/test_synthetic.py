"""SyntheticSource scenarios produce the signals their names promise."""

import math

import pytest

from openpulse.telemetry.frame import Cap
from openpulse.telemetry.synthetic import SCENARIOS, SyntheticSource


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_frames_are_sane(name):
    src = SyntheticSource(name)
    src.open()
    for k in range(400):
        f = src.build_at(k / 200)
        assert f.speed_ms >= 0
        assert -1 <= f.gear <= 8
        assert 0.0 <= f.throttle <= 1.0 and 0.0 <= f.brake <= 1.0
        assert Cap.ND_SLIP in f.caps
        assert all(math.isfinite(x) for x in f.wheel_omega)
    src.close()


def test_lock_fronts_locks_fronts():
    src = SyntheticSource("lock_fronts")
    src.open()
    fs = [src.build_at(k / 100) for k in range(300)]
    src.close()
    braking = [f for f in fs if f.brake > 0.5]
    assert braking
    # front omega much lower than rear omega while braking
    assert min(f.wheel_omega[0] for f in braking) < 0.5 * max(f.wheel_omega[2] for f in braking)


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        SyntheticSource("nope")
