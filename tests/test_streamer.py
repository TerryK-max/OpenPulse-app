"""SampleStreamer paces frames and keeps the box FIFO ahead of real time."""

import time

from openpulse.box.session import BoxSession
from openpulse.config import DeviceConfig, StreamConfig
from openpulse.streamer import SampleStreamer
from openpulse.transport import protocol as P
from openpulse.transport.mock import MockTransport
from openpulse.waveforms import wave_sine


def test_streamer_runs_and_box_keeps_pace():
    s = BoxSession(MockTransport())
    s.connect(DeviceConfig(sample_rate_hz=1000))
    st = SampleStreamer(s, wave_sine, 1000, StreamConfig(batch=8, lookahead_ms=20, status_hz=20))
    st.start()
    try:
        time.sleep(1.0)
    finally:
        st.stop()
        time.sleep(0.1)
        s.request_status()
        time.sleep(0.2)

    snap = s.snapshot().last_stats
    assert snap is not None
    assert snap.mode == P.MODE_SAMPLES
    assert snap.samples_played > 500  # ~1 s at 1 kHz, allow scheduling slop
    assert snap.fifo_overrun == 0
    assert snap.crc_err == 0
    # streamer stayed ahead of playout
    assert st.samples_sent >= snap.samples_played
    s.close()


def test_waveforms_are_in_range():
    for name, fn in [("sine", wave_sine)]:
        vals = [fn(i, 1000) for i in range(2000)]
        assert min(vals) >= 0 and max(vals) <= 127, name
