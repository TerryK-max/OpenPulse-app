"""Validate real Assetto Corsa captures committed under tests/fixtures/.

Skips entirely when there are none — see tests/fixtures/README.md for how to add
one. Once a capture is committed these checks guard against parser / normalizer
regressions on real data.
"""

import math
from pathlib import Path

import pytest

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.frame import Cap
from openpulse.telemetry.record import load_frames

_FIX = Path(__file__).parent / "fixtures"
_CAPS = sorted(_FIX.glob("ac_*.jsonl")) if _FIX.exists() else []


@pytest.mark.skipif(not _CAPS, reason="no AC captures in tests/fixtures/ (see its README)")
@pytest.mark.parametrize("path", _CAPS, ids=lambda p: p.name)
def test_ac_capture_parses_and_derives(path):
    frames = load_frames(path)
    assert len(frames) > 50, "capture too short to be useful"

    assert frames[0].source.startswith("ac/")
    assert frames[0].caps != Cap.NONE

    on_track = [f for f in frames if f.on_track]
    assert on_track, "no on-track frames in the capture"

    dv = Deriver()
    speeds, rpms = [], []
    for raw in frames:
        f = dv.process(raw)
        assert -1 <= f.gear <= 9
        assert 0.0 <= f.throttle <= 1.0 + 1e-3
        assert 0.0 <= f.brake <= 1.0 + 1e-3
        if f.on_track:
            speeds.append(f.speed_ms)
            rpms.append(f.rpm)
        if math.isfinite(f.grip_scrub):
            assert -0.01 <= f.grip_scrub <= 2.0

    assert max(speeds) > 5.0, "car never moved"
    assert max(speeds) < 160.0, "speed_ms implausible — wrong unit? (m/s expected)"
    if any(r > 0 for r in rpms):
        assert max(rpms) < 22000, "rpm implausible"
