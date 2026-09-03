"""In-process fake box — lets the whole app + GUI run with no hardware.

It is a *behavioural* model of the firmware renderer, faithful enough to exercise
the handshake, the version gate, mode changes, the STATUS dashboard and the
DATA_SAMPLES path (PROTOCOL.md, link_control.c). It is not cycle-accurate and it
does not model I2C / thermal physics.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from openpulse.transport import protocol as P
from openpulse.transport.base import Transport, TransportError, TransportStats

_FW_MAJOR, _FW_MINOR = 0, 2
_FIFO_CAP = 256


class MockTransport(Transport):
    kind = "mock"

    def __init__(self, *, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self._out: queue.Queue[bytes] = queue.Queue()
        self._lock = threading.RLock()
        self._open = False
        self._stats = TransportStats()

        # box model state
        self._tx_seq = 0
        self._rx_last_seq: int | None = None
        self._mode = P.MODE_IDLE
        self._data_blocked = False
        self._rate = 1000
        self._failsafe_ms = 100
        self._amp_max = 127
        self._loss_policy = 0
        self._underrun_decay_ms = 20
        self._ol_lra_period = 64
        self._od_clamp = 0xC0

        self._t_boot = self._clock()
        self._t_samples_start: float | None = None
        self._t_last_data: float | None = None
        self._samples_credited = 0
        self._c = _Counters()

    # -- lifecycle ------------------------------------------------------- #
    def open(self) -> None:
        self._open = True
        self._emit_log(P.LOG_INFO, "mock box ready")

    def close(self) -> None:
        self._open = False

    # -- io ------------------------------------------------------------- #
    def send_frame(self, frame: bytes) -> None:
        if not self._open:
            raise TransportError("transport is not open")
        self._stats.frames_sent += 1
        self._stats.bytes_sent += len(frame)
        f = P.parse_frame(frame)
        with self._lock:
            if f is None:
                self._c.crc_err += 1
                return
            self._account_seq(f.seq, f.type)
            self._dispatch(f)

    def read_frame(self, timeout: float) -> bytes | None:
        if not self._open:
            raise TransportError("transport is not open")
        try:
            buf = self._out.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        self._stats.frames_recv += 1
        self._stats.bytes_recv += len(buf)
        return buf

    @property
    def stats(self) -> TransportStats:
        return self._stats

    # -- box model ---------------------------------------------------------- #
    def _account_seq(self, seq: int, type_: int) -> None:
        gap = P.seq_gap(self._rx_last_seq, seq)
        if gap >= P.PROTO_RESYNC_GAP:
            self._resync()
        elif gap:
            self._c.seq_gap_frames += gap
        self._rx_last_seq = seq
        _ = type_

    def _dispatch(self, f: P.Frame) -> None:
        t = f.type
        if t == P.TYPE_RESYNC:
            self._resync()
        elif t == P.TYPE_DATA_SAMPLES:
            self._on_data(len(f.payload))
        elif t == P.TYPE_ENVELOPE:
            self._t_last_data = self._clock()
        elif t == P.TYPE_CTRL_PING:
            nonce = f.payload[0] if f.payload else 0
            self._emit(
                P.TYPE_CTRL_PONG,
                P._PONG.pack(nonce, _FW_MAJOR, _FW_MINOR, P.PROTO_VERSION, self._mode),
            )
        elif t == P.TYPE_CTRL_SET_CONFIG:
            self._on_set_config(f.payload)
        elif t == P.TYPE_CTRL_SET_MODE:
            self._on_set_mode(f.payload)
        elif t == P.TYPE_STATUS_REQ:
            self._emit_status()
        else:
            self._c.bad_type += 1
            self._emit_log(P.LOG_WARN, f"unknown type 0x{t:02X}")

    def _on_data(self, n: int) -> None:
        self._c.frames_rx += 1
        if self._data_blocked or self._mode != P.MODE_SAMPLES:
            return
        self._t_last_data = self._clock()
        space = _FIFO_CAP - self._fifo_fill()
        if n > space:
            self._c.fifo_overrun += 1
            n = max(0, space)
        self._samples_credited += n

    def _on_set_config(self, payload: bytes) -> None:
        if len(payload) != P._CONFIG.size:
            self._emit_fault(P.FAULT_BAD_CONFIG, 0)
            return
        (version, flags, olp, odc, rate, failsafe, amp_max, loss, decay, reserved) = P._CONFIG.unpack(payload)
        if version != P.PROTO_VERSION:
            self._data_blocked = True
            self._emit_fault(P.FAULT_VERSION, P.PROTO_VERSION)
            return
        bad = (
            (flags & ~P.CFG_FLAG_MASK)
            or olp > P.PROTO_OLP_MAX
            or (rate and not (P.PROTO_RATE_MIN <= rate <= P.PROTO_RATE_MAX))
            or (failsafe and not (P.PROTO_FAILSAFE_MIN <= failsafe <= P.PROTO_FAILSAFE_MAX))
            or amp_max > P.PROTO_AMP_MAX
            or loss > 1
            or reserved != 0
        )
        if bad:
            self._emit_fault(P.FAULT_BAD_CONFIG, 0)
            return
        if olp:
            self._ol_lra_period = olp
        if odc:
            self._od_clamp = odc
        if rate:
            self._rate = rate
        if failsafe:
            self._failsafe_ms = failsafe
        if amp_max:
            self._amp_max = amp_max
        self._loss_policy = loss
        if decay:
            self._underrun_decay_ms = decay
        if flags & P.CFG_FLAG_RESET_STATS:
            self._c = _Counters()
            self._samples_credited = 0
            if self._t_samples_start is not None:
                self._t_samples_start = self._clock()
        self._data_blocked = False
        self._emit_status()

    def _on_set_mode(self, payload: bytes) -> None:
        if len(payload) != 1:
            self._emit_fault(P.FAULT_BAD_MODE, 0xFF)
            return
        mode = payload[0]
        if mode > P.MODE_LOCAL_TEST:
            self._emit_fault(P.FAULT_BAD_MODE, mode)
            return
        if self._data_blocked and mode != P.MODE_IDLE:
            self._emit_fault(P.FAULT_VERSION, P.PROTO_VERSION)
            return
        self._mode = mode
        if mode == P.MODE_SAMPLES:
            self._t_samples_start = self._clock()
            self._t_last_data = self._clock()
            self._samples_credited = 0
            self._c.samples_played = 0
            self._emit_log(P.LOG_INFO, "mock: SAMPLES mode")
        self._emit_status()

    def _resync(self) -> None:
        self._rx_last_seq = None
        self._samples_credited = self._played()
        self._c.resync += 1

    # -- derived model quantities --------------------------------------- #
    def _played(self) -> int:
        if self._mode != P.MODE_SAMPLES or self._t_samples_start is None:
            return self._c.samples_played
        want = int((self._clock() - self._t_samples_start) * self._rate)
        return max(self._c.samples_played, min(want, self._samples_credited))

    def _fifo_fill(self) -> int:
        return max(0, min(_FIFO_CAP, self._samples_credited - self._played()))

    def _check_failsafe(self) -> None:
        if self._mode not in (P.MODE_SAMPLES, P.MODE_ENVELOPE):
            return
        if self._t_last_data is None:
            return
        if (self._clock() - self._t_last_data) * 1000 > self._failsafe_ms:
            if not self._c._fs_latched:
                self._c.failsafe_trips += 1
                self._c._fs_latched = True
        else:
            self._c._fs_latched = False

    # -- outbound ------------------------------------------------------- #
    def _emit(self, type_: int, payload: bytes) -> None:
        self._out.put(P.build_frame(type_, self._tx_seq, payload))
        self._tx_seq = (self._tx_seq + 1) & 0xFF

    def _emit_log(self, level: int, text: str) -> None:
        self._emit(P.TYPE_LOG, bytes((level,)) + text.encode("ascii", "replace")[:P.PROTO_MAX_PAYLOAD - 1])

    def _emit_fault(self, code: int, detail: int) -> None:
        self._emit(P.TYPE_FAULT, P._FAULT.pack(code, detail))

    def _emit_status(self) -> None:
        with self._lock:
            self._check_failsafe()
            self._c.samples_played = self._played()
            fill = self._fifo_fill()
            uptime = int(self._clock() - self._t_boot)
            vbat = int(5.15 / 5.6 * 255)  # ~5.15 V
            payload = P._STATS.pack(
                _FW_MAJOR, _FW_MINOR, P.PROTO_VERSION, self._mode,
                uptime, self._c.frames_rx, self._c.samples_played,
                self._c.crc_err, self._c.bad_type, self._c.seq_gap_frames, self._c.resync,
                self._c.fifo_overrun, self._c.fifo_underrun, self._c.i2c_err,
                self._c.failsafe_trips, self._c.tick_backlog_max, fill,
                0xE0, vbat, 0,
            )
        self._emit(P.TYPE_STATUS_REP, payload)


class _Counters:
    __slots__ = (
        "frames_rx", "samples_played", "crc_err", "bad_type", "seq_gap_frames",
        "resync", "fifo_overrun", "fifo_underrun", "i2c_err", "failsafe_trips",
        "tick_backlog_max", "_fs_latched",
    )

    def __init__(self) -> None:
        for s in self.__slots__:
            setattr(self, s, 0)
        self._fs_latched = False
