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
from openpulse.transport import protocol as P

SampleFn = Callable[[int, int], int]  # (index, rate) -> int8 0..127


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
