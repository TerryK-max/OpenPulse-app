"""TelemetryController with the synthetic source (no game)."""

import time

from openpulse.telemetry.controller import TelemetryController
from openpulse.telemetry.frame import Cap


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_synthetic_source_runs_and_enriches():
    tc = TelemetryController()
    tc.start("synthetic", scenario="grip_scrub", poll_hz=200)
    try:
        assert _wait(lambda: tc.snapshot().frames_seen > 5)
        s = tc.snapshot()
        assert s.status == "live"
        assert Cap.ND_SLIP in s.caps
        f = tc.latest_frame()
        assert f is not None and f.dt > 0
        # deriver ran: grip_scrub is populated for this scenario
        assert _wait(lambda: (fr := tc.latest_frame()) is not None
                     and fr.grip_scrub == fr.grip_scrub and fr.grip_scrub > 0.1)
    finally:
        tc.stop()
    assert tc.snapshot().status == "offline"


def test_bad_source_reports_error():
    tc = TelemetryController()
    tc.start("replay", replay_path="")
    try:
        assert _wait(lambda: tc.snapshot().status == "error")
        assert tc.snapshot().detail
    finally:
        tc.stop()


def test_recording(tmp_path):
    tc = TelemetryController()
    tc.start("synthetic", scenario="hotlap", poll_hz=200)
    try:
        assert _wait(lambda: tc.snapshot().frame is not None)
        tc.start_recording(str(tmp_path / "rec.jsonl"))
        assert _wait(lambda: tc.snapshot().record_count > 20)
        tc.stop_recording()
    finally:
        tc.stop()
    assert (tmp_path / "rec.jsonl").exists()
