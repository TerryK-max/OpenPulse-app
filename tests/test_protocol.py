"""Protocol mirror tests — must match OpenPulse-firmware/tools/test/test_link.c
intent and OpenPulse-firmware/docs/PROTOCOL.md.
"""

import struct

import pytest

from openpulse.transport import protocol as P


def test_crc_check_value():
    assert P.crc8_smbus(b"123456789") == 0xF4


def test_struct_sizes_match_proto_h():
    assert P._ENV.size == 2
    assert P._CONFIG.size == 12
    assert P._PONG.size == 5
    assert P._STATS.size == 40
    assert P._FAULT.size == 5


def test_frame_roundtrip():
    f = P.build_frame(P.TYPE_DATA_SAMPLES, 42, bytes(range(8)))
    assert len(f) == 8 + 4
    got = P.parse_frame(f)
    assert got == P.Frame(P.TYPE_DATA_SAMPLES, 42, bytes(range(8)))


def test_seq_wraps():
    f = P.build_frame(P.TYPE_STATUS_REQ, 255, b"")
    assert P.parse_frame(f).seq == 255


@pytest.mark.parametrize(
    "buf",
    [
        b"",
        b"\x01\x00",  # too short
        b"\x01\x00\x05\x00",  # LEN says 5, no payload
        bytes([P.TYPE_STATUS_REQ, 0, 0, 0xFF]),  # bad CRC
        bytes(P.PROTO_MAX_FRAME + 1),  # too long
    ],
)
def test_parse_rejects_malformed(buf):
    assert P.parse_frame(buf) is None


def test_len_mismatch_rejected():
    body = bytes([P.TYPE_DATA_SAMPLES, 0, 4]) + b"\x00\x00\x00"  # LEN 4, 3 bytes
    assert P.parse_frame(body + bytes([P.crc8_smbus(body)])) is None


def test_seq_gap():
    assert P.seq_gap(None, 5) == 0
    assert P.seq_gap(5, 6) == 0
    assert P.seq_gap(5, 8) == 2
    assert P.seq_gap(254, 1) == 2  # wrap
    assert P.seq_gap(10, 10) == 255


def test_config_payload_layout():
    pl = P.config_payload(sample_rate_hz=1000, failsafe_ms=100, flags=P.CFG_FLAG_RESET_STATS)
    version, flags, olp, odc, rate, fs, amp, loss, decay, reserved = P._CONFIG.unpack(pl)
    assert version == P.PROTO_VERSION
    assert flags == P.CFG_FLAG_RESET_STATS
    assert (rate, fs) == (1000, 100)
    assert reserved == 0


def test_parse_stats_fields():
    raw = struct.pack(
        "<BBBB III HHHHHHHHHH BBH",
        0, 2, 1, P.MODE_SAMPLES,
        12, 3400, 3399,
        0, 0, 0, 0, 0, 1, 0, 0, 4, 16,
        0xE0, 233, 0,
    )
    st = P.parse_stats(raw)
    assert st.mode == P.MODE_SAMPLES
    assert st.samples_played == 3399
    assert st.fifo_fill == 16
    assert st.fifo_underrun == 1
    assert not st.over_temp and not st.over_current
    assert round(st.vbat_v, 1) == round(233 * 5.6 / 255, 1)


def test_parse_fault_and_pong():
    assert P.parse_fault(P._FAULT.pack(P.FAULT_VERSION, 1)) == P.Fault(P.FAULT_VERSION, 1)
    pg = P.parse_pong(P._PONG.pack(0xA5, 0, 2, 1, P.MODE_IDLE))
    assert pg.nonce == 0xA5 and pg.fw == "0.2" and pg.proto_version == 1


def test_oversize_payload_rejected():
    with pytest.raises(ValueError):
        P.build_frame(P.TYPE_DATA_SAMPLES, 0, bytes(P.PROTO_MAX_PAYLOAD + 1))
