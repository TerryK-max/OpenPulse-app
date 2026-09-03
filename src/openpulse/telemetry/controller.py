"""TelemetryController — owns a source + Deriver + acquisition thread, publishes
a thread-safe snapshot for the GUI. Optionally records to disk.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

from openpulse.telemetry.base import SessionInfo, SourceStatus, TelemetryError, TelemetrySource
from openpulse.telemetry.derive import DeriveConfig, Deriver
from openpulse.telemetry.frame import Cap, TelemetryFrame
from openpulse.telemetry.record import TelemetryRecorder
from openpulse.telemetry.registry import build_source


@dataclass(slots=True)
class TelemetrySnapshot:
    status: str = "offline"          # offline | connected | live | error
    detail: str = ""
    source_kind: str = ""
    source_active: str = ""
    session: SessionInfo | None = None
    caps: Cap = Cap.NONE
    frame: TelemetryFrame | None = None
    frames_seen: int = 0
    rate_hz: float = 0.0
    recording: bool = False
    record_count: int = 0


class TelemetryController:
    def __init__(self, derive_cfg: DeriveConfig | None = None):
        self._deriver = Deriver(derive_cfg)
        self._source: TelemetrySource | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snap = TelemetrySnapshot()
        self._recorder: TelemetryRecorder | None = None
        self._rate_ema = 0.0
        self._rpm_max_hint = 0.0

    # -- snapshot ------------------------------------------------------- #
    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return self._snap

    def _set(self, **kw: object) -> None:
        with self._lock:
            self._snap = replace(self._snap, **kw)

    def latest_frame(self) -> TelemetryFrame | None:
        with self._lock:
            return self._snap.frame

    # -- lifecycle ---------------------------------------------------------- #
    def start(
        self,
        kind: str,
        *,
        udp_host: str = "127.0.0.1",
        udp_port: int = 9996,
        replay_path: str = "",
        scenario: str = "hotlap",
        poll_hz: float = 333.0,
        rpm_max_hint: float = 0.0,
    ) -> None:
        self.stop()
        self._rpm_max_hint = rpm_max_hint
        self._deriver.reset()
        self._set(status="connecting", detail="", source_kind=kind, frames_seen=0)
        try:
            src = build_source(
                kind,
                udp_host=udp_host,
                udp_port=udp_port,
                replay_path=replay_path,
                scenario=scenario,
                rpm_max_hint=rpm_max_hint,
            )
            src.open()
        except TelemetryError as e:
            self._set(status="error", detail=str(e))
            return
        self._source = src
        self._stop.clear()
        self._poll_timeout = 1.0 / max(1.0, poll_hz)
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.stop_recording()
        if self._source is not None:
            try:
                self._source.close()
            except Exception:
                pass
            self._source = None
        self._set(status="offline", detail="", frame=None, session=None, source_active="")

    # -- recording ---------------------------------------------------------- #
    def start_recording(self, path: str) -> None:
        if self._source is None:
            return
        with self._lock:
            self._recorder = TelemetryRecorder(path, self._source.session_info(), self._source.name)
        self._set(recording=True, record_count=0)

    def stop_recording(self) -> None:
        with self._lock:
            rec = self._recorder
            self._recorder = None
        if rec is not None:
            rec.close()
        self._set(recording=False)

    # -- acquisition loop ------------------------------------------------- #
    def _run(self) -> None:
        src = self._source
        assert src is not None
        last = time.perf_counter()
        seen = 0
        while not self._stop.is_set():
            try:
                raw = src.poll(self._poll_timeout)
            except TelemetryError as e:
                self._set(status="error", detail=str(e))
                return
            st = src.status()
            active = getattr(src, "active_name", src.name)
            if raw is None:
                self._set(
                    status={
                        SourceStatus.OFFLINE: "offline",
                        SourceStatus.CONNECTED: "connected",
                        SourceStatus.LIVE: "live",
                    }[st],
                    source_active=active,
                    session=src.session_info(),
                )
                continue

            frame = self._deriver.process(raw)
            now = time.perf_counter()
            dt = now - last
            last = now
            if dt > 0:
                inst = 1.0 / dt
                self._rate_ema = inst if self._rate_ema == 0 else self._rate_ema + 0.05 * (inst - self._rate_ema)
            seen += 1

            with self._lock:
                rec = self._recorder
            if rec is not None:
                try:
                    rec.write(frame)
                except Exception:
                    pass

            self._set(
                status="live" if frame.on_track else "connected",
                source_active=active,
                session=src.session_info(),
                caps=frame.caps,
                frame=frame,
                frames_seen=seen,
                rate_hz=self._rate_ema,
                record_count=rec.count if rec is not None else 0,
            )
