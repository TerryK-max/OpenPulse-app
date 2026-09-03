"""Shared helpers for effects."""

from __future__ import annotations

import math

from openpulse.telemetry.frame import TelemetryFrame


def finite(xs) -> list[float]:
    return [x for x in xs if isinstance(x, float) and math.isfinite(x)]


def axle_max(vals, front: bool) -> float:
    sl = slice(0, 2) if front else slice(2, 4)
    f = finite(vals[sl])
    return max(f) if f else math.nan


def axle_min(vals, front: bool) -> float:
    sl = slice(0, 2) if front else slice(2, 4)
    f = finite(vals[sl])
    return min(f) if f else math.nan


def speed_curve(speed_ms: float, v_min: float, v_ref: float, curve: float) -> float:
    if v_ref <= v_min:
        return 1.0
    t = (speed_ms - v_min) / (v_ref - v_min)
    return max(0.0, min(1.0, t)) ** curve


def frame_is_active(f: TelemetryFrame) -> bool:
    """Roughly: the car is on track and moving under control."""
    return f.on_track and not f.paused
