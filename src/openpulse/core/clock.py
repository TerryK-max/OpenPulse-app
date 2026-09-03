"""Timing helpers: OS timer resolution + a real-time sample pacer.

The pacing model is the one proven at the bench in
``OpenPulse-firmware/tools/pc_sender/openpulse_send.py``: derive the number of
samples that *should* have been sent by now from elapsed wall-clock time, and
keep the box FIFO a fixed ``lookahead`` ahead of that.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator


@contextlib.contextmanager
def high_res_timers() -> Iterator[None]:
    """On Windows, request 1 ms scheduler granularity for the duration of the
    block (games do this). No-op elsewhere."""
    if sys.platform == "win32":
        import ctypes

        winmm = ctypes.WinDLL("winmm")
        winmm.timeBeginPeriod(1)
        try:
            yield
        finally:
            winmm.timeEndPeriod(1)
    else:
        yield


class Pacer:
    """Tracks how many samples should have been produced since ``start()``."""

    def __init__(self, rate_hz: int, lookahead_samples: int):
        self.rate_hz = rate_hz
        self.lookahead = lookahead_samples
        self._t0 = 0.0

    def start(self, now: float | None = None) -> None:
        self._t0 = time.perf_counter() if now is None else now

    def elapsed(self, now: float | None = None) -> float:
        return (time.perf_counter() if now is None else now) - self._t0

    def target_index(self, now: float | None = None) -> int:
        """Sample index the box should have *consumed* by now, plus lookahead."""
        return int(self.elapsed(now) * self.rate_hz) + self.lookahead
