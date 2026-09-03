"""Assetto Corsa shared-memory source (``Local\\acpmf_*``).

Windows-only — the mapped objects are named Win32 sections. On other OSes use
the UDP source (that is the whole point of the multi-PC setup: game on Windows,
box on another machine reading the UDP stream).
"""

from __future__ import annotations

import ctypes
import sys
import time

from openpulse.telemetry.ac import structs as S
from openpulse.telemetry.ac.normalize import normalize_shm
from openpulse.telemetry.base import (
    SessionInfo,
    SourceStatus,
    TelemetrySource,
    TelemetryUnavailable,
)
from openpulse.telemetry.frame import Cap, TelemetryFrame

# generous map length so newer builds / CSP (which append fields) still map;
# we only ever parse the documented prefix.
_MAP_PHYS = max(ctypes.sizeof(S.ACPhysics), 2048)
_MAP_GRAPH = max(ctypes.sizeof(S.ACGraphics), 2048)
_MAP_STAT = max(ctypes.sizeof(S.ACStatic), 2048)


class ACSharedMemorySource(TelemetrySource):
    name = "ac/shm"

    def __init__(self) -> None:
        self._open = False
        self._mm: dict[str, object] = {}
        self._static: S.ACStatic | None = None
        self._session: SessionInfo | None = None
        self._last_pid = -1
        self._last_status = S.STATUS_OFF

    # -- lifecycle ------------------------------------------------------- #
    def open(self) -> None:
        if self._open:
            return
        if sys.platform != "win32":
            raise TelemetryUnavailable(
                "the Assetto Corsa shared-memory source is Windows-only; "
                "use the UDP source to read telemetry from another PC"
            )
        import mmap

        try:
            self._mm = {
                "phys": mmap.mmap(-1, _MAP_PHYS, S.PHYSICS_TAG, access=mmap.ACCESS_READ),
                "graph": mmap.mmap(-1, _MAP_GRAPH, S.GRAPHICS_TAG, access=mmap.ACCESS_READ),
                "stat": mmap.mmap(-1, _MAP_STAT, S.STATIC_TAG, access=mmap.ACCESS_READ),
            }
        except OSError as e:
            raise TelemetryUnavailable(
                f"cannot map the AC shared memory ({e}). Is Assetto Corsa running?"
            ) from e
        self._open = True

    def close(self) -> None:
        for m in self._mm.values():
            try:
                m.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._mm.clear()
        self._open = False

    # -- reads --------------------------------------------------------- #
    def _read(self, key: str, typ: type[ctypes.Structure]) -> ctypes.Structure:
        m = self._mm[key]
        return typ.from_buffer_copy(m[: ctypes.sizeof(typ)])  # type: ignore[index]

    def _refresh_static(self) -> None:
        st = self._read("stat", S.ACStatic)
        self._static = st  # type: ignore[assignment]
        self._session = SessionInfo(
            game="Assetto Corsa",
            car=st.carModel,
            track=st.track,
            track_config=st.trackConfiguration,
            driver=(f"{st.playerName} {st.playerSurname}".strip() or st.playerNick),
            rpm_max=float(st.maxRpm),
        )

    # -- TelemetrySource --------------------------------------------------- #
    def status(self) -> SourceStatus:
        if not self._open:
            return SourceStatus.OFFLINE
        try:
            g = self._read("graph", S.ACGraphics)
        except Exception:
            return SourceStatus.OFFLINE
        if g.status == S.STATUS_OFF:
            return SourceStatus.OFFLINE
        return SourceStatus.LIVE if g.status == S.STATUS_LIVE else SourceStatus.CONNECTED

    def capabilities(self) -> Cap:
        # constant for this source (LOAD / TYRE_RADIUS / RPM_MAX depend on the
        # car and are decided per-frame in normalize_shm)
        return (
            Cap.SUSP_TRAVEL_ABS | Cap.TYRE_TEMP | Cap.TYRE_PRESSURE | Cap.TYRE_WEAR
            | Cap.COMBINED_SLIP | Cap.WHEEL_OMEGA | Cap.OFF_TRACK_COUNT | Cap.TYRE_DIRT
            | Cap.DAMAGE | Cap.SURFACE_GRIP | Cap.LOCAL_VELOCITY | Cap.YAW_RATE
            | Cap.DRS | Cap.SESSION_STATE
        )

    def session_info(self) -> SessionInfo | None:
        return self._session

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._open:
            return None
        deadline = time.perf_counter() + max(0.0, timeout)
        while True:
            try:
                g = self._read("graph", S.ACGraphics)
                p = self._read("phys", S.ACPhysics)
            except Exception:
                return None
            if self._static is None or g.status != self._last_status:
                self._refresh_static()
            self._last_status = g.status
            if g.status == S.STATUS_OFF:
                return None
            if p.packetId != self._last_pid or time.perf_counter() >= deadline:
                self._last_pid = p.packetId
                assert self._static is not None
                return normalize_shm(p, g, self._static, t_mono=time.perf_counter())
            time.sleep(0.001)
