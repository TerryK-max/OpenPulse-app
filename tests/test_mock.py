"""MockTransport behaviour vs link_control.c / PROTOCOL.md."""

import pytest

from openpulse.transport import protocol as P
from openpulse.transport.mock import MockTransport


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def mock():
    clk = Clock()
    m = MockTransport(clock=clk)
    m.open()
    return m, clk


def _send(m, type_, seq, payload=b""):
    m.send_frame(P.build_frame(type_, seq, payload))


def _drain(m):
    out = []
    while (b := m.read_frame(0.0)) is not None:
        out.append(P.parse_frame(b))
    return out


def test_ping_pong(mock):
    m, _ = mock
    _send(m, P.TYPE_CTRL_PING, 0, b"\x5a")
    frames = _drain(m)
    pongs = [f for f in frames if f.type == P.TYPE_CTRL_PONG]
    assert pongs and P.parse_pong(pongs[0].payload).nonce == 0x5A


def test_set_config_ok_then_status(mock):
    m, _ = mock
    _send(m, P.TYPE_CTRL_SET_CONFIG, 0, P.config_payload(sample_rate_hz=2000, failsafe_ms=120))
    reps = [f for f in _drain(m) if f.type == P.TYPE_STATUS_REP]
    assert reps
    st = P.parse_stats(reps[-1].payload)
    assert st.proto_version == P.PROTO_VERSION


def test_set_config_bad_field_faults(mock):
    m, _ = mock
    bad = P._CONFIG.pack(P.PROTO_VERSION, 0, 0, 0, 99, 0, 0, 0, 0, 0)  # rate 99 < min
    m.send_frame(P.build_frame(P.TYPE_CTRL_SET_CONFIG, 0, bad))
    faults = [f for f in _drain(m) if f.type == P.TYPE_FAULT]
    assert faults and P.parse_fault(faults[0].payload).code == P.FAULT_BAD_CONFIG


def test_version_mismatch_blocks_data_plane(mock):
    m, _ = mock
    bad = P._CONFIG.pack(2, 0, 0, 0, 0, 0, 0, 0, 0, 0)  # version 2
    m.send_frame(P.build_frame(P.TYPE_CTRL_SET_CONFIG, 0, bad))
    faults = [f for f in _drain(m) if f.type == P.TYPE_FAULT]
    assert faults and P.parse_fault(faults[0].payload).code == P.FAULT_VERSION

    # SET_MODE(SAMPLES) refused while blocked
    _send(m, P.TYPE_CTRL_SET_MODE, 1, bytes((P.MODE_SAMPLES,)))
    faults = [f for f in _drain(m) if f.type == P.TYPE_FAULT]
    assert faults and P.parse_fault(faults[-1].payload).code == P.FAULT_VERSION

    # a good config clears the block
    _send(m, P.TYPE_CTRL_SET_CONFIG, 2, P.config_payload(sample_rate_hz=1000))
    _send(m, P.TYPE_CTRL_SET_MODE, 3, bytes((P.MODE_SAMPLES,)))
    reps = [f for f in _drain(m) if f.type == P.TYPE_STATUS_REP]
    assert P.parse_stats(reps[-1].payload).mode == P.MODE_SAMPLES


def test_bad_mode_faults(mock):
    m, _ = mock
    _send(m, P.TYPE_CTRL_SET_MODE, 0, bytes((9,)))
    faults = [f for f in _drain(m) if f.type == P.TYPE_FAULT]
    assert faults and P.parse_fault(faults[0].payload).code == P.FAULT_BAD_MODE


def test_bad_crc_counted_not_answered(mock):
    m, _ = mock
    good = P.build_frame(P.TYPE_STATUS_REQ, 0, b"")
    m.send_frame(good[:-1] + bytes((good[-1] ^ 0xFF,)))
    _send(m, P.TYPE_STATUS_REQ, 1)
    st = P.parse_stats([f for f in _drain(m) if f.type == P.TYPE_STATUS_REP][-1].payload)
    assert st.crc_err == 1


def test_samples_played_advances_with_clock(mock):
    m, clk = mock
    _send(m, P.TYPE_CTRL_SET_CONFIG, 0, P.config_payload(sample_rate_hz=1000))
    _send(m, P.TYPE_CTRL_SET_MODE, 1, bytes((P.MODE_SAMPLES,)))
    # credit 500 samples
    for k in range(10):
        _send(m, P.TYPE_DATA_SAMPLES, 2 + k, bytes(50))
    clk.t += 0.25  # 250 ms -> 250 samples playable
    _send(m, P.TYPE_STATUS_REQ, 20)
    st = P.parse_stats([f for f in _drain(m) if f.type == P.TYPE_STATUS_REP][-1].payload)
    assert 200 <= st.samples_played <= 300
    assert st.fifo_fill > 0


def test_failsafe_trips_after_silence(mock):
    m, clk = mock
    _send(m, P.TYPE_CTRL_SET_CONFIG, 0, P.config_payload(sample_rate_hz=1000, failsafe_ms=100))
    _send(m, P.TYPE_CTRL_SET_MODE, 1, bytes((P.MODE_SAMPLES,)))
    _send(m, P.TYPE_DATA_SAMPLES, 2, bytes(8))
    clk.t += 0.5  # silence well past failsafe_ms
    _send(m, P.TYPE_STATUS_REQ, 3)
    st = P.parse_stats([f for f in _drain(m) if f.type == P.TYPE_STATUS_REP][-1].payload)
    assert st.failsafe_trips == 1


def test_big_seq_gap_is_resync(mock):
    m, _ = mock
    _send(m, P.TYPE_STATUS_REQ, 0)
    _drain(m)
    _send(m, P.TYPE_STATUS_REQ, 50)  # gap 49 >= 16
    st = P.parse_stats([f for f in _drain(m) if f.type == P.TYPE_STATUS_REP][-1].payload)
    assert st.resync >= 1
