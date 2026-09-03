"""Named scalar maps telemetry -> amplitude 0..1, for the Phase-B debug bridge
(:class:`openpulse.streamer.TelemetryMapStreamer`). Lets you feel one channel of
the telemetry through the box before the effect engine exists.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from openpulse.telemetry.frame import TelemetryFrame

AmpMap = Callable[[TelemetryFrame | None], float]


def _c(x: float) -> float:
    return 0.0 if not math.isfinite(x) else max(0.0, min(1.0, x))


def _finite_max(xs) -> float:
    vals = [x for x in xs if math.isfinite(x)]
    return max(vals) if vals else math.nan


def _finite_min(xs) -> float:
    vals = [x for x in xs if math.isfinite(x)]
    return min(vals) if vals else math.nan


def m_off(f: TelemetryFrame | None) -> float:
    return 0.0


def m_speed(f: TelemetryFrame | None) -> float:
    return _c(f.speed_ms / 80.0) if f else 0.0


def m_rpm(f: TelemetryFrame | None) -> float:
    if not f or f.rpm_max <= 0:
        return 0.0
    return _c((f.rpm / f.rpm_max - 0.3) / 0.7)


def m_throttle(f: TelemetryFrame | None) -> float:
    return _c(f.throttle) if f else 0.0


def m_brake(f: TelemetryFrame | None) -> float:
    return _c(f.brake) if f else 0.0


def m_grip_scrub(f: TelemetryFrame | None) -> float:
    return _c(f.grip_scrub) if f else 0.0


def m_combined_slip(f: TelemetryFrame | None) -> float:
    if not f:
        return 0.0
    return _c(_finite_max(f.combined_slip) / 8.0)


def m_lockup(f: TelemetryFrame | None) -> float:
    if not f:
        return 0.0
    fr = _finite_min(f.lock_ratio[:2])
    if not math.isfinite(fr) or f.brake < 0.15:
        return 0.0
    return _c((0.9 - fr) / 0.9)


def m_wheelspin(f: TelemetryFrame | None) -> float:
    if not f:
        return 0.0
    re = _finite_max(f.lock_ratio[2:])
    if not math.isfinite(re) or f.throttle < 0.15:
        return 0.0
    return _c((re - 1.05) / 0.6)


def m_susp_vel(f: TelemetryFrame | None) -> float:
    if not f:
        return 0.0
    v = _finite_max(tuple(abs(x) for x in f.susp_vel_ms))
    return _c(v / 0.5) if math.isfinite(v) else 0.0


DEBUG_MAPS: dict[str, AmpMap] = {
    "off": m_off,
    "speed": m_speed,
    "rpm": m_rpm,
    "throttle": m_throttle,
    "brake": m_brake,
    "grip_scrub": m_grip_scrub,
    "combined_slip": m_combined_slip,
    "lockup": m_lockup,
    "wheelspin": m_wheelspin,
    "susp_vel": m_susp_vel,
}
