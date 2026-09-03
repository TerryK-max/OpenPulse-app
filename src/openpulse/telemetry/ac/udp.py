"""Assetto Corsa UDP remote-telemetry source (port 9996).

Protocol: send a 12-byte handshake (operationId 0), receive HandshakerResponse
(408 B), send SUBSCRIBE_UPDATE (operationId 1), then receive RTCarInfo (328 B)
per physics step. Send DISMISS (operationId 3) on close. Cross-platform — works
against Assetto Corsa running on another machine.
"""

from __future__ import annotations

import socket
import time

from openpulse.telemetry.ac import structs as S
from openpulse.telemetry.ac.normalize import normalize_udp
from openpulse.telemetry.base import (
    SessionInfo,
    SourceStatus,
    TelemetrySource,
    TelemetryUnavailable,
)
from openpulse.telemetry.frame import Cap, TelemetryFrame

_REHANDSHAKE_AFTER_S = 2.0


def _req(op: int) -> bytes:
    return bytes(S.HandshakeRequest(identifier=1, version=1, operationId=op))


class ACUDPSource(TelemetrySource):
    name = "ac/udp"

    def __init__(self, host: str = "127.0.0.1", port: int = S.UDP_PORT, rpm_max_hint: float = 0.0):
        self.host = host
        self.port = port
        self._rpm_max_hint = rpm_max_hint
        self._open = False
        self._sock: socket.socket | None = None
        self._session: SessionInfo | None = None
        self._packet_id = 0
        self._last_rx = 0.0
        self._got_car_data = False

    # -- lifecycle ------------------------------------------------------- #
    def open(self) -> None:
        if self._open:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(1.0)
        except OSError as e:
            raise TelemetryUnavailable(f"cannot open UDP socket: {e}") from e
        self._open = True
        try:
            self._handshake()
        except (OSError, TelemetryUnavailable):
            # game may not be up yet — stay open and retry in poll()
            self._session = None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.sendto(_req(S.OP_DISMISS), (self.host, self.port))
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._open = False

    # -- protocol ---------------------------------------------------------- #
    def _handshake(self) -> None:
        assert self._sock is not None
        self._sock.sendto(_req(S.OP_HANDSHAKE), (self.host, self.port))
        data, _ = self._sock.recvfrom(2048)
        if len(data) < S.HANDSHAKE_RESPONSE_SIZE:
            raise TelemetryUnavailable(f"short handshake response ({len(data)} B)")
        hr = S.HandshakerResponse.from_buffer_copy(data[: S.HANDSHAKE_RESPONSE_SIZE])
        self._session = SessionInfo(
            game="Assetto Corsa",
            car=hr.carName,
            track=hr.trackName,
            track_config=hr.trackConfig,
            driver=hr.driverName,
            rpm_max=self._rpm_max_hint,
        )
        self._sock.sendto(_req(S.OP_SUBSCRIBE_UPDATE), (self.host, self.port))
        self._last_rx = time.perf_counter()

    # -- TelemetrySource --------------------------------------------------- #
    def status(self) -> SourceStatus:
        if not self._open:
            return SourceStatus.OFFLINE
        if not self._got_car_data or (time.perf_counter() - self._last_rx) > _REHANDSHAKE_AFTER_S:
            return SourceStatus.OFFLINE if self._session is None else SourceStatus.CONNECTED
        return SourceStatus.LIVE

    def capabilities(self) -> Cap:
        return (
            Cap.SLIP_RATIO | Cap.SLIP_ANGLE | Cap.COMBINED_SLIP | Cap.ND_SLIP
            | Cap.WHEEL_OMEGA | Cap.TYRE_RADIUS | Cap.LOAD | Cap.TYRE_DIRT
            | Cap.ABS_FLAG | Cap.TC_FLAG | Cap.ENGINE_LIMITER_FLAG
        )

    def session_info(self) -> SessionInfo | None:
        return self._session

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._open or self._sock is None:
            return None

        if self._session is None or (time.perf_counter() - self._last_rx) > _REHANDSHAKE_AFTER_S:
            try:
                self._handshake()
            except OSError:
                return None

        self._sock.settimeout(max(0.001, timeout))
        try:
            data, _ = self._sock.recvfrom(2048)
        except (TimeoutError, OSError):
            return None

        self._last_rx = time.perf_counter()
        if len(data) >= S.RTCARINFO_SIZE and data[:1] == b"a":
            rt = S.RTCarInfo.from_buffer_copy(data[: S.RTCARINFO_SIZE])
            if rt.size not in (0, S.RTCARINFO_SIZE):  # sanity vs the packet's own length field
                return None
            self._got_car_data = True
            self._packet_id += 1
            return normalize_udp(
                rt,
                t_mono=self._last_rx,
                packet_id=self._packet_id,
                rpm_max_hint=self._session.rpm_max if self._session else self._rpm_max_hint,
            )
        return None  # RTLap (212 B) / handshake echo (408 B) / unknown — ignore

    # let a caller supply a per-car rev limit (UDP has no maxRpm)
    def set_rpm_max_hint(self, rpm_max: float) -> None:
        self._rpm_max_hint = rpm_max
        if self._session is not None:
            self._session.rpm_max = rpm_max
