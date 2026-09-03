"""BoxSession — the high-level conversation with the box over any Transport.

Owns the outbound SEQ counter, a background reader thread that parses inbound
frames, the connect handshake (RESYNC -> PING -> SET_CONFIG -> SET_MODE) and the
protocol-version check (PROTOCOL.md §7). Thread-safe: workers read snapshots.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from openpulse.config import DeviceConfig
from openpulse.transport import protocol as P
from openpulse.transport.base import Transport, TransportError


class ProtocolMismatch(Exception):
    """The box speaks a different protocol version than this app."""

    def __init__(self, box_version: int, box_fw: str):
        super().__init__(
            f"protocol mismatch: box speaks v{box_version} (fw {box_fw}), "
            f"app speaks v{P.PROTO_VERSION}"
        )
        self.box_version = box_version
        self.box_fw = box_fw


@dataclass(slots=True)
class HandshakeResult:
    fw: str
    proto_version: int
    mode: int


@dataclass(slots=True)
class SessionState:
    connected: bool = False
    handshake: HandshakeResult | None = None
    last_stats: P.Stats | None = None
    last_pong: P.Pong | None = None
    rtt_ms: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    faults: deque[P.Fault] = field(default_factory=lambda: deque(maxlen=50))
    logs: deque[P.LogLine] = field(default_factory=lambda: deque(maxlen=200))
    mode: int = P.MODE_IDLE
    frames_in: int = 0
    frames_out: int = 0
    last_frame_in_at: float = 0.0


class BoxSession:
    def __init__(self, transport: Transport):
        self._t = transport
        self._tx_seq = 0
        self._tx_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._s = SessionState()

        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._ping_waits: dict[int, float] = {}  # nonce -> perf_counter at send
        self._on_frame: Callable[[P.Frame], None] | None = None

    # -- lifecycle ------------------------------------------------------- #
    def connect(
        self,
        device: DeviceConfig | None = None,
        *,
        enter_samples: bool = False,
        reset_stats: bool = True,
        ping_timeout: float = 1.0,
    ) -> HandshakeResult:
        """Open the transport, start the reader, run the handshake.

        Raises ProtocolMismatch on a version mismatch, TransportError on a pipe
        failure, TimeoutError if the box never answers the PING.
        """
        device = device or DeviceConfig()
        self._t.open()
        self._start_reader()

        # RESYNC first: reseats the box SEQ tracking and makes its local
        # self-test step aside before anything is gap-accounted (bench fix,
        # firmware ROADMAP 3.5).
        self.send(P.TYPE_RESYNC)
        nonce = 0xA5
        deadline = time.perf_counter() + ping_timeout
        self.send(P.TYPE_CTRL_PING, bytes((nonce,)))
        self.send(P.TYPE_CTRL_SET_CONFIG, device.to_payload(reset_stats=reset_stats))
        if enter_samples:
            self.send(P.TYPE_CTRL_SET_MODE, bytes((P.MODE_SAMPLES,)))

        pong: P.Pong | None = None
        while time.perf_counter() < deadline:
            with self._state_lock:
                pong = self._s.last_pong
            if pong is not None and pong.nonce == nonce:
                break
            time.sleep(0.005)
        if pong is None:
            self.close()
            raise TimeoutError(f"box did not answer PING within {ping_timeout:.1f}s")
        if pong.proto_version != P.PROTO_VERSION:
            self.close()
            raise ProtocolMismatch(pong.proto_version, pong.fw)

        hs = HandshakeResult(pong.fw, pong.proto_version, pong.mode)
        with self._state_lock:
            self._s.connected = True
            self._s.handshake = hs
        return hs

    def close(self) -> None:
        try:
            if self._t.is_open:
                self.send(P.TYPE_CTRL_SET_MODE, bytes((P.MODE_IDLE,)))
        except TransportError:
            pass
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
        self._t.close()
        with self._state_lock:
            self._s.connected = False

    # -- outbound ------------------------------------------------------- #
    def send(self, type_: int, payload: bytes = b"") -> None:
        with self._tx_lock:
            frame = P.build_frame(type_, self._tx_seq, payload)
            self._tx_seq = (self._tx_seq + 1) & 0xFF
        self._t.send_frame(frame)
        with self._state_lock:
            self._s.frames_out += 1

    def send_samples(self, samples: bytes | list[int]) -> None:
        self.send(P.TYPE_DATA_SAMPLES, P.samples_payload(samples))

    def set_mode(self, mode: int) -> None:
        self.send(P.TYPE_CTRL_SET_MODE, bytes((mode & 0xFF,)))

    def request_status(self) -> None:
        self.send(P.TYPE_STATUS_REQ)

    def resync(self) -> None:
        self.send(P.TYPE_RESYNC)

    def ping(self, nonce: int) -> None:
        with self._state_lock:
            self._ping_waits[nonce & 0xFF] = time.perf_counter()
        self.send(P.TYPE_CTRL_PING, bytes((nonce & 0xFF,)))

    def ping_blocking(self, nonce: int, timeout: float = 0.5) -> float | None:
        """Send a PING and return the round-trip time in ms, or None on timeout."""
        self.ping(nonce)
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            with self._state_lock:
                p = self._s.last_pong
                if p is not None and p.nonce == (nonce & 0xFF) and self._s.rtt_ms:
                    return self._s.rtt_ms[-1]
            time.sleep(0.002)
        return None

    # -- inbound ------------------------------------------------------- #
    def set_frame_hook(self, hook: Callable[[P.Frame], None] | None) -> None:
        self._on_frame = hook

    def _start_reader(self) -> None:
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name="box-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                buf = self._t.read_frame(timeout=0.2)
            except TransportError:
                if self._stop.is_set():
                    return
                time.sleep(0.05)
                continue
            if buf is None:
                continue
            f = P.parse_frame(buf)
            if f is None:
                continue
            now = time.perf_counter()
            with self._state_lock:
                self._s.frames_in += 1
                self._s.last_frame_in_at = now
            self._handle(f, now)
            if self._on_frame is not None:
                try:
                    self._on_frame(f)
                except Exception:
                    pass

    def _handle(self, f: P.Frame, now: float) -> None:
        if f.type == P.TYPE_STATUS_REP and len(f.payload) == P._STATS.size:
            st = P.parse_stats(f.payload)
            with self._state_lock:
                self._s.last_stats = st
                self._s.mode = st.mode
        elif f.type == P.TYPE_CTRL_PONG and len(f.payload) == P._PONG.size:
            pg = P.parse_pong(f.payload)
            with self._state_lock:
                self._s.last_pong = pg
                self._s.mode = pg.mode
                t0 = self._ping_waits.pop(pg.nonce, None)
                if t0 is not None:
                    self._s.rtt_ms.append((now - t0) * 1000)
        elif f.type == P.TYPE_LOG:
            ln = P.parse_log(f.payload)
            with self._state_lock:
                self._s.logs.append(ln)
        elif f.type == P.TYPE_FAULT and len(f.payload) == P._FAULT.size:
            ft = P.parse_fault(f.payload)
            with self._state_lock:
                self._s.faults.append(ft)

    # -- snapshot ----------------------------------------------------- #
    def snapshot(self) -> SessionState:
        with self._state_lock:
            s = self._s
            return SessionState(
                connected=s.connected,
                handshake=s.handshake,
                last_stats=s.last_stats,
                last_pong=s.last_pong,
                rtt_ms=deque(s.rtt_ms, maxlen=200),
                faults=deque(s.faults, maxlen=50),
                logs=deque(s.logs, maxlen=200),
                mode=s.mode,
                frames_in=s.frames_in,
                frames_out=s.frames_out,
                last_frame_in_at=s.last_frame_in_at,
            )
