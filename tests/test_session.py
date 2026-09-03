"""BoxSession against the MockTransport (no hardware)."""

import time

import pytest

from openpulse.box.session import BoxSession, ProtocolMismatch
from openpulse.config import DeviceConfig
from openpulse.transport import protocol as P
from openpulse.transport.base import Transport, TransportStats
from openpulse.transport.mock import MockTransport


def test_connect_handshake_and_version_ok():
    s = BoxSession(MockTransport())
    hs = s.connect(DeviceConfig(sample_rate_hz=1500))
    try:
        assert hs.proto_version == P.PROTO_VERSION
        assert hs.fw == "0.2"
        # status should arrive from the SET_CONFIG reply
        for _ in range(50):
            if s.snapshot().last_stats is not None:
                break
            time.sleep(0.02)
        assert s.snapshot().last_stats is not None
    finally:
        s.close()


def test_ping_rtt_measured():
    s = BoxSession(MockTransport())
    s.connect(DeviceConfig())
    try:
        rtt = s.ping_blocking(0x11, timeout=1.0)
        assert rtt is not None and rtt >= 0.0
    finally:
        s.close()


def test_stream_samples_advances_box():
    s = BoxSession(MockTransport())
    s.connect(DeviceConfig(sample_rate_hz=1000))
    try:
        s.set_mode(P.MODE_SAMPLES)
        for _ in range(30):
            s.send_samples(bytes(8))
            time.sleep(0.005)
        s.request_status()
        for _ in range(50):
            st = s.snapshot().last_stats
            if st and st.frames_rx >= 30:
                break
            time.sleep(0.02)
        st = s.snapshot().last_stats
        assert st is not None and st.frames_rx >= 30
        assert st.mode == P.MODE_SAMPLES
    finally:
        s.close()


class _MismatchTransport(Transport):
    kind = "fake"

    def __init__(self):
        self._open = False
        self._out = []

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def send_frame(self, frame):
        f = P.parse_frame(frame)
        if f and f.type == P.TYPE_CTRL_PING:
            nonce = f.payload[0] if f.payload else 0
            self._out.append(P.build_frame(P.TYPE_CTRL_PONG, 0, P._PONG.pack(nonce, 9, 9, 2, 0)))

    def read_frame(self, timeout):
        time.sleep(min(timeout, 0.01))
        return self._out.pop(0) if self._out else None

    @property
    def stats(self):
        return TransportStats()


def test_protocol_mismatch_raises():
    s = BoxSession(_MismatchTransport())
    with pytest.raises(ProtocolMismatch):
        s.connect(DeviceConfig())
