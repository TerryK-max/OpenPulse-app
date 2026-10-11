"""BoxController — the object the GUI talks to.

Owns the transport + session + streamer, runs connect/stream work off the GUI
thread, and publishes a plain snapshot the GUI polls each frame.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from openpulse.box.session import BoxSession, ProtocolMismatch
from openpulse.config import AppSettings
from openpulse.transport import protocol as P
from openpulse.transport.base import Transport, TransportError
from openpulse.transport.mock import MockTransport
from openpulse.transport.usb import UsbVendorTransport
from openpulse.waveforms import WAVEFORMS


@dataclass(slots=True)
class ControllerSnapshot:
    status: str = "disconnected"  # disconnected | connecting | connected | error | streaming
    detail: str = ""
    transport_kind: str = ""
    fw: str = ""
    proto_ok: bool = False
    mode: int = P.MODE_IDLE
    streaming: bool = False
    stream_kind: str = ""          # "" | "waveform" | "telemetry" | "engine"
    stream_error: str = ""
    samples_sent: int = 0
    bridge_amp: float = math.nan
    stats: P.Stats | None = None
    rtt_ms: tuple[float, ...] = ()
    faults: tuple[str, ...] = ()
    logs: tuple[str, ...] = ()
    frames_in: int = 0
    frames_out: int = 0


class BoxController:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()
        self._session: BoxSession | None = None
        self._streamer = None
        self._pipeline = None
        self._lock = threading.Lock()
        self._snap = ControllerSnapshot()
        self._ping_nonce = 0
        self._stream_kind = ""

    def pipeline(self):  # noqa: ANN201 - openpulse.engine.Pipeline | None
        return self._pipeline

    # -- snapshot ---------------------------------------------------------- #
    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            return self._snap

    def _set(self, **kw: object) -> None:
        with self._lock:
            self._snap = replace(self._snap, **kw)

    def _refresh_from_session(self) -> None:
        s = self._session
        if s is None:
            return
        st = s.snapshot()
        streaming = self._streamer is not None and self._streamer.running

        if self._pipeline is not None and st.last_stats is not None:
            drv_ot = st.last_stats.over_temp or any(
                f.name == "DRV_OT" for f in st.faults
            )
            self._pipeline.set_external_limit(0.0 if drv_ot else 1.0)

        self._set(
            status="streaming" if streaming else ("connected" if st.connected else "disconnected"),
            fw=st.handshake.fw if st.handshake else "",
            proto_ok=bool(st.handshake and st.handshake.proto_version == P.PROTO_VERSION),
            mode=st.mode,
            streaming=streaming,
            stream_kind=self._stream_kind if streaming else "",
            stats=st.last_stats,
            rtt_ms=tuple(st.rtt_ms),
            faults=tuple(f"{f.name} detail={f.detail}" for f in st.faults),
            logs=tuple(f"[{ln.level_name}] {ln.text}" for ln in st.logs),
            frames_in=st.frames_in,
            frames_out=st.frames_out,
            samples_sent=getattr(self._streamer, "samples_sent", 0) if self._streamer else 0,
            bridge_amp=getattr(self._streamer, "current_amp", math.nan) if self._streamer else math.nan,
            stream_error=getattr(self._streamer, "error", None) or "" if self._streamer else "",
        )

    # -- actions (called from a worker thread) --------------------------- #
    def connect_async(self) -> None:
        threading.Thread(target=self._connect, name="ctl-connect", daemon=True).start()

    def _make_transport(self) -> Transport:
        if self.settings.transport == "mock":
            return MockTransport()
        return UsbVendorTransport(self.settings.usb_vid, self.settings.usb_pid)

    def _connect(self) -> None:
        self.disconnect()
        transport = self._make_transport()
        self._set(status="connecting", detail="", transport_kind=transport.kind)
        session = BoxSession(transport)
        try:
            hs = session.connect(self.settings.device, reset_stats=True)
        except ProtocolMismatch as e:
            self._set(status="error", detail=str(e))
            return
        except Exception as e:  # noqa: BLE001 - any connect failure is surfaced, not fatal
            try:
                session.close()
            except Exception:
                pass
            self._set(status="error", detail=str(e))
            return
        self._session = session
        self._set(status="connected", detail=f"fw {hs.fw}, proto v{hs.proto_version}")
        threading.Thread(target=self._poll_loop, name="ctl-poll", daemon=True).start()

    def disconnect(self) -> None:
        if self._streamer is not None:
            self._streamer.stop()
            self._streamer = None
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._set(status="disconnected", detail="", streaming=False, fw="", proto_ok=False)

    def start_stream(self) -> None:
        from openpulse.streamer import SampleStreamer

        if self._session is None or self._streamer is not None:
            return
        fn = WAVEFORMS.get(self.settings.stream.waveform, WAVEFORMS["sim"])
        self._streamer = SampleStreamer(
            self._session, fn, self.settings.device.sample_rate_hz, self.settings.stream
        )
        self._stream_kind = "waveform"
        self._streamer.start()

    def start_telemetry_bridge(
        self,
        frame_getter: Callable[[], object],
        amp_fn: Callable[[object], float],
    ) -> None:
        """Phase-B debug: stream one telemetry-derived scalar to the box."""
        from openpulse.streamer import TelemetryMapStreamer

        if self._session is None or self._streamer is not None:
            return
        self._streamer = TelemetryMapStreamer(
            self._session, frame_getter, amp_fn,  # type: ignore[arg-type]
            self.settings.device.sample_rate_hz, self.settings.stream,
        )
        self._stream_kind = "telemetry"
        self._streamer.start()

    def set_bridge_amp_fn(self, amp_fn: Callable[[object], float]) -> None:
        if self._streamer is not None and hasattr(self._streamer, "set_amp_fn"):
            self._streamer.set_amp_fn(amp_fn)  # type: ignore[attr-defined]

    def start_engine(self, frame_getter: Callable[[], object]) -> None:
        """Phase C: stream the effect engine's output to the box."""
        from openpulse.engine.pipeline import Pipeline
        from openpulse.streamer import PipelineStreamer

        if self._session is None:
            self._set(status="error", detail="connect the box first (Connection panel)")
            return
        if self._streamer is not None:      # replace whatever was streaming
            self._streamer.stop()
            self._streamer = None
        self._pipeline = Pipeline(self.settings.engine)
        self._streamer = PipelineStreamer(
            self._session, self._pipeline, frame_getter,  # type: ignore[arg-type]
            self.settings.device.sample_rate_hz, self.settings.stream,
            dither=self.settings.stream.dither,
        )
        self._stream_kind = "engine"
        self._streamer.start()

    def trigger_effect_test(self, effect_id: str) -> bool:
        """False when the engine isn't running (nothing to play it through)."""
        pipe = self._pipeline
        return pipe is not None and pipe.trigger_test(effect_id)

    def stop_stream(self) -> None:
        if self._streamer is not None:
            self._streamer.stop()
            self._streamer = None
        self._pipeline = None
        self._stream_kind = ""
        if self._session is not None:
            self._session.set_mode(P.MODE_IDLE)

    def ping_burst(self, n: int = 50) -> None:
        def run() -> None:
            s = self._session
            if s is None:
                return
            for _ in range(n):
                if self._session is not s:
                    return
                self._ping_nonce = (self._ping_nonce % 250) + 1
                try:
                    s.ping_blocking(self._ping_nonce, timeout=0.3)
                except TransportError:
                    return
                time.sleep(0.01)

        threading.Thread(target=run, name="ctl-ping", daemon=True).start()

    def _poll_loop(self) -> None:
        while self._session is not None:
            self._refresh_from_session()
            time.sleep(0.1)
