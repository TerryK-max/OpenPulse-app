"""OpenPulse wire protocol v1 — Python mirror of the firmware's src/link/proto.h.

    >>> KEEP THIS FILE IN SYNC WITH  OpenPulse-firmware/src/link/proto.h  <<<

Authoritative spec: OpenPulse-firmware/docs/PROTOCOL.md. Any change to frame
layout, TYPE_* values, struct field order/size/meaning or the CRC on the firmware
side must be mirrored here in the same change. The module-level self-tests at the
bottom (struct sizes, CRC vector) fail loudly on drift.

Frame:  [TYPE][SEQ][LEN][PAYLOAD x LEN][CRC8]      total = LEN + 4, range 4..60
    CRC8 = CRC-8/SMBUS (poly 0x07, init 0x00, no reflection, no final XOR)
           over bytes [0 .. 3+LEN-1]  (TYPE, SEQ, LEN, PAYLOAD).
All multi-byte integers are little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
#  constants (proto.h)
# --------------------------------------------------------------------------- #
PROTO_VERSION = 1

PROTO_MAX_PAYLOAD = 56
PROTO_FRAME_OVERHEAD = 4  # TYPE + SEQ + LEN + CRC8
PROTO_MAX_FRAME = PROTO_MAX_PAYLOAD + PROTO_FRAME_OVERHEAD  # 60
PROTO_MIN_FRAME = PROTO_FRAME_OVERHEAD  # 4

PROTO_OFF_TYPE = 0
PROTO_OFF_SEQ = 1
PROTO_OFF_LEN = 2
PROTO_OFF_PAYLOAD = 3

PROTO_RESYNC_GAP = 16

# config_msg field ranges (link_control.c validates these)
PROTO_RATE_MIN = 250
PROTO_RATE_MAX = 4000
PROTO_FAILSAFE_MIN = 10
PROTO_FAILSAFE_MAX = 2000
PROTO_OLP_MAX = 127
PROTO_AMP_MAX = 127

# config_msg.flags bits
CFG_FLAG_PERSIST = 0x01  # bit0: persist to DataFlash (firmware Phase 6)
CFG_FLAG_RESET_STATS = 0x02  # bit1: zero the STATUS_REP counters
CFG_FLAG_MASK = 0x03

# --------------------------------------------------------------------------- #
#  message types
# --------------------------------------------------------------------------- #
# PC -> box
TYPE_DATA_SAMPLES = 0x01
TYPE_ENVELOPE = 0x02
TYPE_CTRL_SET_CONFIG = 0x10
TYPE_CTRL_SET_MODE = 0x11
TYPE_CTRL_PING = 0x12
TYPE_STATUS_REQ = 0x20
TYPE_RESYNC = 0x7F
# box -> PC
TYPE_CTRL_PONG = 0x81
TYPE_STATUS_REP = 0x82
TYPE_LOG = 0x83
TYPE_FAULT = 0x84

TYPE_NAME = {
    v: k
    for k, v in list(globals().items())
    if k.startswith("TYPE_") and isinstance(v, int)
}

# modes (payload of TYPE_CTRL_SET_MODE)
MODE_IDLE = 0
MODE_SAMPLES = 1
MODE_ENVELOPE = 2
MODE_LOCAL_TEST = 3
MODE_NAME = {
    MODE_IDLE: "IDLE",
    MODE_SAMPLES: "SAMPLES",
    MODE_ENVELOPE: "ENVELOPE",
    MODE_LOCAL_TEST: "LOCAL_TEST",
}

# fault codes (payload byte 0 of TYPE_FAULT)
FAULT_BAD_CONFIG = 1
FAULT_BAD_MODE = 2
FAULT_VERSION = 3
FAULT_I2C = 4
FAULT_DRV_OT = 5
FAULT_INTERNAL = 6
FAULT_NAME = {
    FAULT_BAD_CONFIG: "BAD_CONFIG",
    FAULT_BAD_MODE: "BAD_MODE",
    FAULT_VERSION: "VERSION",
    FAULT_I2C: "I2C",
    FAULT_DRV_OT: "DRV_OT",
    FAULT_INTERNAL: "INTERNAL",
}

# log levels (payload byte 0 of TYPE_LOG)
LOG_ERR, LOG_WARN, LOG_INFO, LOG_DBG = 0, 1, 2, 3
LOG_LEVEL_NAME = {LOG_ERR: "ERR", LOG_WARN: "WARN", LOG_INFO: "INFO", LOG_DBG: "DBG"}


# --------------------------------------------------------------------------- #
#  CRC-8 / SMBUS  (src/util/crc.c)
# --------------------------------------------------------------------------- #
def crc8_smbus(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
    return c


# --------------------------------------------------------------------------- #
#  framing
# --------------------------------------------------------------------------- #
def build_frame(type_: int, seq: int, payload: bytes = b"") -> bytes:
    """Assemble one wire frame. Raises ValueError on an over-long payload."""
    if len(payload) > PROTO_MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} > {PROTO_MAX_PAYLOAD}")
    body = bytes((type_ & 0xFF, seq & 0xFF, len(payload))) + payload
    return body + bytes((crc8_smbus(body),))


@dataclass(frozen=True, slots=True)
class Frame:
    type: int
    seq: int
    payload: bytes


def parse_frame(buf: bytes) -> Frame | None:
    """Return a Frame, or None if the bytes are not a valid frame."""
    if len(buf) < PROTO_MIN_FRAME or len(buf) > PROTO_MAX_FRAME:
        return None
    plen = buf[PROTO_OFF_LEN]
    if plen + PROTO_FRAME_OVERHEAD != len(buf):
        return None
    if crc8_smbus(buf[:-1]) != buf[-1]:
        return None
    payload = bytes(buf[PROTO_OFF_PAYLOAD:PROTO_OFF_PAYLOAD + plen])
    return Frame(buf[PROTO_OFF_TYPE], buf[PROTO_OFF_SEQ], payload)


def seq_gap(last_seq: int | None, seq: int) -> int:
    """Frames missing between last_seq and seq, per PROTOCOL.md §2."""
    if last_seq is None:
        return 0
    return (seq - last_seq - 1) & 0xFF


# --------------------------------------------------------------------------- #
#  payload structs (little-endian, packed) — proto.h
# --------------------------------------------------------------------------- #
_ENV = struct.Struct("<BB")  # env_cmd: target, slew_ms  (2 B)
_CONFIG = struct.Struct("<BBBBHHBBBB")  # config_msg (12 B)
_PONG = struct.Struct("<BBBBB")  # pong_msg (5 B)
_STATS = struct.Struct("<BBBB III HHHHHHHHHH BBH")  # stats_msg (40 B)
_FAULT = struct.Struct("<BI")  # fault payload: code, detail (5 B)


def config_payload(
    *,
    sample_rate_hz: int = 0,
    failsafe_ms: int = 0,
    ol_lra_period: int = 0,
    od_clamp: int = 0,
    amp_max: int = 0,
    loss_policy: int = 0,
    underrun_decay_ms: int = 0,
    flags: int = 0,
) -> bytes:
    """Pack a config_msg. 0 = "keep current" for every unit-bearing field."""
    return _CONFIG.pack(
        PROTO_VERSION,
        flags,
        ol_lra_period,
        od_clamp,
        sample_rate_hz,
        failsafe_ms,
        amp_max,
        loss_policy,
        underrun_decay_ms,
        0,  # reserved, must be 0
    )


def envelope_payload(target: int, slew_ms: int) -> bytes:
    return _ENV.pack(target & 0xFF, slew_ms & 0xFF)


def samples_payload(samples: bytes | bytearray | list[int]) -> bytes:
    """int8 amplitude samples -> payload bytes (two's complement)."""
    return bytes((s & 0xFF) for s in samples)


@dataclass(frozen=True, slots=True)
class Pong:
    nonce: int
    fw_major: int
    fw_minor: int
    proto_version: int
    mode: int

    @property
    def fw(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}"


@dataclass(frozen=True, slots=True)
class Stats:
    fw_major: int
    fw_minor: int
    proto_version: int
    mode: int
    uptime_s: int
    frames_rx: int
    samples_played: int
    crc_err: int
    bad_type: int
    seq_gap_frames: int
    resync: int
    fifo_overrun: int
    fifo_underrun: int
    i2c_err: int
    failsafe_trips: int
    tick_backlog_max: int
    fifo_fill: int
    drv_status: int
    drv_vbat: int
    reserved: int

    @property
    def fw(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}"

    @property
    def vbat_v(self) -> float:
        return self.drv_vbat * 5.6 / 255

    @property
    def over_temp(self) -> bool:
        return bool(self.drv_status & 0x02)

    @property
    def over_current(self) -> bool:
        return bool(self.drv_status & 0x01)


@dataclass(frozen=True, slots=True)
class Fault:
    code: int
    detail: int

    @property
    def name(self) -> str:
        return FAULT_NAME.get(self.code, f"0x{self.code:02X}")


@dataclass(frozen=True, slots=True)
class LogLine:
    level: int
    text: str

    @property
    def level_name(self) -> str:
        return LOG_LEVEL_NAME.get(self.level, "?")


def parse_pong(payload: bytes) -> Pong:
    return Pong(*_PONG.unpack(payload))


def parse_stats(payload: bytes) -> Stats:
    return Stats(*_STATS.unpack(payload))


def parse_fault(payload: bytes) -> Fault:
    return Fault(*_FAULT.unpack(payload))


def parse_log(payload: bytes) -> LogLine:
    level = payload[0] if payload else LOG_INFO
    return LogLine(level, payload[1:].decode("ascii", "replace"))


# --------------------------------------------------------------------------- #
#  self-tests — run at import, fail loudly on drift vs proto.h
# --------------------------------------------------------------------------- #
assert _ENV.size == 2, "env_cmd must be 2 bytes"
assert _CONFIG.size == 12, "config_msg must be 12 bytes"
assert _PONG.size == 5, "pong_msg must be 5 bytes"
assert _STATS.size == 40, "stats_msg must be 40 bytes"
assert _FAULT.size == 5, "fault payload must be 5 bytes"
assert crc8_smbus(b"123456789") == 0xF4, "CRC-8/SMBUS check value"
assert parse_frame(build_frame(TYPE_CTRL_PING, 7, b"\xa5")) == Frame(TYPE_CTRL_PING, 7, b"\xa5")
