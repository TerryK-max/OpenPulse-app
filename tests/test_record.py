"""Recorder + ReplaySource round-trip."""

import math
import time

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.record import ReplaySource, TelemetryRecorder, frame_from_json, frame_to_json, load_frames
from openpulse.telemetry.synthetic import SyntheticSource


def test_frame_json_roundtrip():
    src = SyntheticSource("grip_scrub")
    src.open()
    f = Deriver().process(src.build_at(1.0))
    src.close()
    g = frame_from_json(frame_to_json(f))
    assert g.source == f.source
    assert g.caps == f.caps
    assert g.speed_ms == f.speed_ms
    assert g.nd_slip == f.nd_slip
    assert (math.isclose(g.grip_scrub, f.grip_scrub)
            or (math.isnan(g.grip_scrub) and math.isnan(f.grip_scrub)))


def test_recorder_and_replay(tmp_path):
    path = tmp_path / "cap.jsonl"
    src = SyntheticSource("hotlap", rate_hz=200)
    src.open()
    dv = Deriver()
    with TelemetryRecorder(path, src.session_info(), src.name) as rec:
        for k in range(400):
            rec.write(dv.process(src.build_at(k / 200)))
    src.close()
    assert rec.count == 400
    assert (tmp_path / "cap.jsonl.meta.json").exists()

    frames = load_frames(path)
    assert len(frames) == 400

    rp = ReplaySource(path, speed=50.0, loop=False)
    rp.open()
    got = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        fr = rp.poll(0.05)
        if fr is None and got > 0:
            break
        if fr is not None:
            got += 1
    rp.close()
    assert got > 100
    assert rp.session_info() is not None
