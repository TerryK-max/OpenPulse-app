"""SampleStreamer — paces a waveform to the box in DATA_SAMPLES frames.

Phase A stand-in for the Phase C renderer. It keeps the box FIFO ``lookahead_ms``
ahead of real time (the bench-proven model), batches ``batch`` samples per frame,
and polls STATUS at ``status_hz``. Runs in its own thread.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from openpulse.box.session import BoxSession
from openpulse.config import StreamConfig
from openpulse.core.clock import Pacer, high_res_timers
from openpulse.telemetry.frame import TelemetryFrame
from openpulse.transport import protocol as P

SampleFn = Callable[[int, int], int]  # (index, rate) -> int8 0..127
AmpFn = Callable[[TelemetryFrame | None], float]  # frame -> amplitude 0..1


class SampleStreamer:
    def __init__(self, session: BoxSession, sample_fn: SampleFn, rate_hz: int, cfg: StreamConfig):
        self._session = session
        self._fn = sample_fn
        self._rate = rate_hz
        self._cfg = cfg
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._i = 0  # next sample index to send
        self._lock = threading.Lock()

    @property
    def samples_sent(self) -> int:
        with self._lock:
            return self._i

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sample-streamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------------------------------------------------------- #
    def _batch(self, start: int, n: int) -> bytes:
        return bytes((self._fn(start + k, self._rate) & 0xFF) for k in range(n))

    def _run(self) -> None:
        batch = self._cfg.batch
        lookahead = max(batch, int(self._cfg.lookahead_ms * self._rate / 1000))
        pacer = Pacer(self._rate, lookahead)
        status_period = 1.0 / self._cfg.status_hz

        with high_res_timers():
            self._session.set_mode(P.MODE_SAMPLES)

            # prime the FIFO before pacing so the box never plays an empty buffer
            self._i = 0
            while self._i < lookahead and not self._stop.is_set():
                self._session.send_samples(self._batch(self._i, batch))
                with self._lock:
                    self._i += batch
                time.sleep(0.001)

            pacer.start()
            next_status = time.perf_counter()
            while not self._stop.is_set():
                now = time.perf_counter()
                target = pacer.target_index(now)
                while self._i < target and not self._stop.is_set():
                    self._session.send_samples(self._batch(self._i, batch))
                    with self._lock:
                        self._i += batch
                if now >= next_status:
                    self._session.request_status()
                    next_status += status_period
                time.sleep(0.002)


# --------------------------------------------------------------------------- #
class TelemetryMapStreamer:
    """Phase-B debug bridge: stream a single scalar mapped from live telemetry
    straight to the box, so the whole chain can be felt before the effect
    engine exists. ``amp_fn`` returns 0..1; the value is slew-limited and held
    between telemetry updates.
    """

    def __init__(
        self,
        session: BoxSession,
        frame_getter: Callable[[], TelemetryFrame | None],
        amp_fn: AmpFn,
        rate_hz: int,
        cfg: StreamConfig,
        *,
        slew_per_s: float = 8.0,
    ):
        self._session = session
        self._get = frame_getter
        self._amp_fn = amp_fn
        self._rate = rate_hz
        self._cfg = cfg
        self._slew = slew_per_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._i = 0
        self._amp = 0.0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def samples_sent(self) -> int:
        return self._i

    @property
    def current_amp(self) -> float:
        with self._lock:
            return self._amp

    def set_amp_fn(self, fn: AmpFn) -> None:
        self._amp_fn = fn

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telemetry-map-streamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _block(self, n: int, dt: float) -> bytes:
        try:
            target = max(0.0, min(1.0, float(self._amp_fn(self._get()))))
        except Exception:
            target = 0.0
        step = self._slew * dt / max(1, n)
        out = bytearray(n)
        a = self._amp
        for k in range(n):
            if a < target:
                a = min(target, a + step)
            elif a > target:
                a = max(target, a - step)
            out[k] = int(max(0.0, min(1.0, a)) * 127)
        with self._lock:
            self._amp = a
        return bytes(out)

    def _run(self) -> None:
        batch = self._cfg.batch
        lookahead = max(batch, int(self._cfg.lookahead_ms * self._rate / 1000))
        pacer = Pacer(self._rate, lookahead)
        status_period = 1.0 / self._cfg.status_hz
        block_dt = batch / self._rate

        with high_res_timers():
            self._session.set_mode(P.MODE_SAMPLES)
            self._i = 0
            while self._i < lookahead and not self._stop.is_set():
                self._session.send_samples(self._block(batch, block_dt))
                self._i += batch
                time.sleep(0.001)

            pacer.start()
            next_status = time.perf_counter()
            while not self._stop.is_set():
                now = time.perf_counter()
                target = pacer.target_index(now)
                while self._i < target and not self._stop.is_set():
                    self._session.send_samples(self._block(batch, block_dt))
                    self._i += batch
                if now >= next_status:
                    self._session.request_status()
                    next_status += status_period
                time.sleep(0.002)


# --------------------------------------------------------------------------- #
class PipelineStreamer:
    """Streams the effect engine's output to the box: pulls the latest telemetry
    frame each block, feeds the Pipeline, quantizes to int8, paces the send
    exactly like SampleStreamer."""

    def __init__(
        self,
        session: BoxSession,
        pipeline,  # openpulse.engine.Pipeline
        frame_getter: Callable[[], TelemetryFrame | None],
        rate_hz: int,
        cfg: StreamConfig,
        *,
        dither: bool = False,
    ):
        self._session = session
        self._pipeline = pipeline
        self._get = frame_getter
        self._rate = rate_hz
        self._cfg = cfg
        self._dither = dither
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._i = 0
        self._rng = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def samples_sent(self) -> int:
        return self._i

    @property
    def current_amp(self) -> float:
        return float(self._pipeline.meters().output_peak)

    def start(self) -> None:
        if self._thread is not None:
            return
        import numpy as np

        self._rng = np.random.default_rng(1)
        self._pipeline.reset()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pipeline-streamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _block(self, n: int) -> bytes:
        import numpy as np

        self._pipeline.set_frame(self._get())
        sig = self._pipeline.generate(n, self._rate)
        x = np.clip(sig, 0.0, 1.0) * 127.0
        if self._dither:
            x = x + (self._rng.random(n) - 0.5)
        return np.clip(np.round(x), 0, 127).astype(np.uint8).tobytes()

    def _run(self) -> None:
        batch = self._cfg.batch
        lookahead = max(batch, int(self._cfg.lookahead_ms * self._rate / 1000))
        pacer = Pacer(self._rate, lookahead)
        status_period = 1.0 / self._cfg.status_hz

        with high_res_timers():
            self._session.set_mode(P.MODE_SAMPLES)
            self._i = 0
            while self._i < lookahead and not self._stop.is_set():
                self._session.send_samples(self._block(batch))
                self._i += batch
                time.sleep(0.001)

            pacer.start()
            next_status = time.perf_counter()
            while not self._stop.is_set():
                now = time.perf_counter()
                target = pacer.target_index(now)
                while self._i < target and not self._stop.is_set():
                    self._session.send_samples(self._block(batch))
                    self._i += batch
                if now >= next_status:
                    self._session.request_status()
                    next_status += status_period
                time.sleep(0.002)
