"""Deriver — cross-frame quantities on synthetic scenarios."""

import math

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.frame import Cap
from openpulse.telemetry.synthetic import SyntheticSource


def _run(scenario: str, seconds: float = 4.0, rate: float = 200.0):
    src = SyntheticSource(scenario, rate_hz=rate)
    src.open()
    dv = Deriver()
    out = []
    n = int(seconds * rate)
    for k in range(n):
        raw = src.build_at(k / rate)
        out.append(dv.process(raw))
    src.close()
    return out


def test_dt_and_rpm_max_observed():
    frames = _run("hotlap", 2.0)
    assert all(0 < f.dt <= 0.1 for f in frames[1:])
    assert frames[-1].rpm_max > 0


def test_lock_ratio_lockup():
    frames = _run("lock_fronts", 3.0)
    braking = [f for f in frames if f.brake > 0.5 and f.speed_ms > 3]
    assert braking
    # front wheels barely turning -> lock ratio well below 1
    assert min(min(f.lock_ratio[0], f.lock_ratio[1]) for f in braking) < 0.3
    # rears still rolling
    assert max(f.lock_ratio[2] for f in braking if math.isfinite(f.lock_ratio[2])) > 0.8


def test_lock_ratio_wheelspin():
    frames = _run("wheelspin", 3.0)
    spin = [f for f in frames if f.throttle > 0.5]
    assert max(max(f.lock_ratio[2], f.lock_ratio[3]) for f in spin) > 1.3


def test_grip_scrub_rises_at_limit():
    frames = _run("grip_scrub", 4.0)
    scrub = [f.grip_scrub for f in frames if math.isfinite(f.grip_scrub)]
    assert scrub
    assert max(scrub) > 0.4          # tyres near the limit -> meaningful scrub
    assert min(scrub) < 0.6          # and it dips when nd_slip drops
    # a scenario with low slip has ~zero scrub
    calm = _run("hotlap", 2.0)
    calm_scrub = [f.grip_scrub for f in calm if math.isfinite(f.grip_scrub)]
    assert max(calm_scrub) < 0.3


def test_impact_detected():
    frames = _run("impact", 4.0)
    peak = max(f.impact_g for f in frames)
    assert peak > 5.0
    # impact decays back down afterwards
    assert frames[-1].impact_g < peak


def test_gear_change_edge():
    frames = _run("hotlap", 8.0)
    ups = [i for i, f in enumerate(frames) if f.gear_change == 1]
    assert ups
    # a gear_change is a single-frame edge
    for i in ups:
        assert frames[i + 1].gear_change == 0


def test_grip_scrub_from_combined_slip_when_no_nd_slip():
    # simulate an SHM-style frame: COMBINED_SLIP but no ND_SLIP
    from dataclasses import replace

    src = SyntheticSource("grip_scrub", rate_hz=100)
    src.open()
    dv = Deriver()
    got = []
    for k in range(200):
        raw = src.build_at(k / 100)
        raw = replace(raw, nd_slip=(math.nan,) * 4, caps=(raw.caps & ~Cap.ND_SLIP))
        got.append(dv.process(raw).grip_scrub)
    assert max(v for v in got if math.isfinite(v)) > 0.3
