"""Full engine pipeline: synthetic scenarios + the committed real lap fixture."""

import time
from pathlib import Path

import numpy as np
import pytest

from openpulse.config import AppSettings
from openpulse.engine.pipeline import Pipeline
from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.frame import TelemetryFrame
from openpulse.telemetry.record import load_frames
from openpulse.telemetry.synthetic import SCENARIOS, SyntheticSource

FS = 1000
BLOCK = 12
_FIX = Path(__file__).parent / "fixtures" / "ac_shm_lap.jsonl"


def _drive(pipe: Pipeline, scenario: str, seconds: float):
    src = SyntheticSource(scenario, rate_hz=333)
    src.open()
    dv = Deriver()
    peaks = []
    for k in range(int(seconds * 333)):
        f = dv.process(src.build_at(k / 333))
        pipe.set_frame(f)
        # a few output blocks per telemetry frame
        for _ in range(3):
            sig = pipe.generate(BLOCK, FS)
            assert sig.dtype == np.float32 and sig.shape == (BLOCK,)
            assert sig.min() >= 0.0 and sig.max() <= 1.0
            peaks.append(float(sig.max()))
    src.close()
    return np.array(peaks)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_pipeline_runs_bounded(scenario):
    pipe = Pipeline(AppSettings().engine)
    peaks = _drive(pipe, scenario, 3.0)
    assert peaks.max() <= 1.0
    # something comes through while driving
    assert peaks.mean() > 0.01


def test_gate_closes_when_not_on_track():
    pipe = Pipeline(AppSettings().engine)
    idle = TelemetryFrame(t_mono=0.0, source="idle", on_track=False)
    last = None
    for _ in range(300):
        pipe.set_frame(idle)
        last = pipe.generate(BLOCK, FS)
    assert float(last.max()) == 0.0
    assert pipe.meters().gate_gain < 0.05


def test_stale_telemetry_ramps_down():
    pipe = Pipeline(AppSettings().engine)
    src = SyntheticSource("hotlap", rate_hz=333)
    src.open()
    dv = Deriver()
    for k in range(400):
        pipe.set_frame(dv.process(src.build_at(k / 333)))
        pipe.generate(BLOCK, FS)
    src.close()
    assert pipe.meters().enable > 0.5
    # stop feeding frames, keep generating
    time.sleep(pipe.cfg.stale_ms / 1000 + 0.05)
    for _ in range(200):
        out = pipe.generate(BLOCK, FS)
    assert pipe.meters().enable < 0.05
    assert float(out.max()) == 0.0


@pytest.mark.skipif(not _FIX.exists(), reason="no AC lap fixture")
def test_real_lap_through_pipeline():
    frames = load_frames(_FIX)
    pipe = Pipeline(AppSettings().engine)
    dv = Deriver()
    peak = 0.0
    ms_sum = 0.0
    m = 0
    for raw in frames:
        f = dv.process(raw)
        pipe.set_frame(f)
        sig = pipe.generate(24, 2000)
        assert sig.min() >= 0.0 and sig.max() <= 1.0
        peak = max(peak, float(sig.max()))
        ms_sum += float(np.mean(sig * sig))
        m += 1
    rms = (ms_sum / m) ** 0.5
    assert peak <= 1.0
    assert 0.05 < rms < 0.6      # audible but not pinned
