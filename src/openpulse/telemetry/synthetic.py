"""SyntheticSource — scripted telemetry for tuning effects and demos with no
game running. Also the substrate for deterministic effect tests.

Each scenario is a function ``(t: float) -> dict`` of field overrides; the source
fills a plausible baseline and applies them, at a fixed rate.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from openpulse.telemetry.base import SessionInfo, SourceStatus, TelemetrySource
from openpulse.telemetry.frame import Cap, TelemetryFrame

Scenario = Callable[[float], dict]

_CAPS = (
    Cap.SLIP_RATIO | Cap.SLIP_ANGLE | Cap.COMBINED_SLIP | Cap.ND_SLIP | Cap.WHEEL_OMEGA
    | Cap.TYRE_RADIUS | Cap.LOAD | Cap.SUSP_TRAVEL_ABS | Cap.TYRE_TEMP | Cap.RPM_MAX
    | Cap.OFF_TRACK_COUNT | Cap.ABS_FLAG | Cap.TC_FLAG | Cap.ENGINE_LIMITER_FLAG
    | Cap.SURFACE_GRIP | Cap.SESSION_STATE | Cap.DAMAGE
)
_R = 0.33  # tyre radius


def _rolling(speed: float) -> tuple[float, float, float, float]:
    w = speed / _R
    return (w, w, w, w)


def scn_hotlap(t: float) -> dict:
    """Steady quick lap: mild rumble, gentle corner load, an upshift every ~4 s,
    a kerb strike every ~6 s."""
    speed = 45 + 8 * math.sin(t * 0.15)
    d: dict = {
        "speed_ms": speed,
        "rpm": 6000 + 1500 * abs(math.sin(t * 0.6)),
        "gear": 3 + int(t // 4) % 3,
        "throttle": 0.7 + 0.3 * math.sin(t * 0.5),
        "brake": max(0.0, -math.sin(t * 0.3)) * 0.4,
        "wheel_omega": _rolling(speed),
        "nd_slip": (0.55, 0.55, 0.6, 0.6),
        "accel_g": (1.2 * math.sin(t * 0.3), 0.4 * math.cos(t * 0.5), 0.0),
    }
    if (t % 6.0) < 0.15:
        d["susp_travel_m"] = (0.06, 0.02, 0.055, 0.02)  # kerb hit, left side
        d["off_track_wheels"] = 1
    if (t % 4.0) < 0.05:
        d["gear"] = d["gear"] + 1  # upshift edge
    return d


def scn_lock_fronts(t: float) -> dict:
    speed = max(2.0, 40 - 12 * t if t < 3 else 4.0)
    braking = t > 0.4
    return {
        "speed_ms": speed,
        "brake": 1.0 if braking else 0.1,
        "throttle": 0.0,
        "rpm": 3000,
        "gear": 3,
        "wheel_omega": (
            (speed / _R * (0.15 if braking else 1.0)),
            (speed / _R * (0.15 if braking else 1.0)),
            speed / _R,
            speed / _R,
        ),
        "nd_slip": (1.4, 1.4, 0.5, 0.5) if braking else (0.4,) * 4,
        "abs_active": False,
        "abs_available": False,
    }


def scn_wheelspin(t: float) -> dict:
    speed = 6 + 2 * t
    return {
        "speed_ms": speed,
        "throttle": 1.0,
        "brake": 0.0,
        "rpm": 7200,
        "gear": 2,
        "wheel_omega": (speed / _R, speed / _R, speed / _R * 1.8, speed / _R * 1.8),
        "nd_slip": (0.5, 0.5, 1.5, 1.5),
        "tc_active": False,
        "tc_available": False,
    }


def scn_grip_scrub(t: float) -> dict:
    """The 'tyres squealing at the limit but not a big slide' cue: nd_slip rides
    just around 1.0, oscillating, no lockup, no spin."""
    n = 0.9 + 0.18 * math.sin(t * 2.0) + 0.05 * math.sin(t * 11.0)
    speed = 30 + 3 * math.sin(t * 0.4)
    return {
        "speed_ms": speed,
        "throttle": 0.5,
        "brake": 0.1,
        "rpm": 5500,
        "gear": 3,
        "wheel_omega": _rolling(speed),
        "nd_slip": (n, n, n * 0.96, n * 0.96),
        "combined_slip": (n * 3, n * 3, n * 2.9, n * 2.9),
        "slip_angle": (0.09 * math.sin(t * 2.0),) * 4,
        "accel_g": (1.4 * math.sin(t * 0.5), 0.2, 0.0),
    }


def scn_spin(t: float) -> dict:
    speed = max(3.0, 35 - 6 * t)
    yaw = min(3.0, 0.3 * t * t)
    return {
        "speed_ms": speed,
        "throttle": 0.2,
        "brake": 0.0,
        "rpm": 4000,
        "gear": 2,
        "steer": 1.0,
        "wheel_omega": _rolling(speed),
        "nd_slip": (1.3, 0.8, 1.6, 1.1),
        "slip_angle": (0.6, 0.5, 0.9, 0.8),
        "yaw_rate": yaw,
        "accel_g": (2.2, -0.5, 0.0),
    }


def scn_impact(t: float) -> dict:
    d = scn_hotlap(t)
    if 2.0 <= t < 2.05:
        d["accel_g"] = (14.0, -22.0, 3.0)
        d["damage"] = (0.4, 0.0, 0.0, 0.0, 0.0)
    return d


SCENARIOS: dict[str, Scenario] = {
    "hotlap": scn_hotlap,
    "lock_fronts": scn_lock_fronts,
    "wheelspin": scn_wheelspin,
    "grip_scrub": scn_grip_scrub,
    "spin": scn_spin,
    "impact": scn_impact,
}


class SyntheticSource(TelemetrySource):
    name = "synthetic"

    def __init__(self, scenario: str = "hotlap", *, rate_hz: float = 200.0, clock: Callable[[], float] | None = None):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; have {sorted(SCENARIOS)}")
        self.scenario = scenario
        self._fn = SCENARIOS[scenario]
        self._period = 1.0 / rate_hz
        self._clock = clock or time.perf_counter
        self._open = False
        self._t0 = 0.0
        self._pid = 0
        self._next = 0.0

    def open(self) -> None:
        self._t0 = self._clock()
        self._next = self._t0
        self._pid = 0
        self._open = True

    def close(self) -> None:
        self._open = False

    def status(self) -> SourceStatus:
        return SourceStatus.LIVE if self._open else SourceStatus.OFFLINE

    def capabilities(self) -> Cap:
        return _CAPS

    def session_info(self) -> SessionInfo | None:
        return SessionInfo(game="synthetic", car=f"scenario:{self.scenario}", track="test", rpm_max=8000)

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._open:
            return None
        now = self._clock()
        deadline = now + max(0.0, timeout)
        while now < self._next:
            if now >= deadline:
                return None
            time.sleep(min(self._period, self._next - now, 0.002))
            now = self._clock()
        self._next += self._period
        self._pid += 1
        return self._build(now - self._t0, now)

    def build_at(self, t: float) -> TelemetryFrame:
        """Deterministic frame at sim-time ``t`` (for tests)."""
        self._pid += 1
        return self._build(t, t)

    def _build(self, t: float, t_mono: float) -> TelemetryFrame:
        speed = 30.0
        base = dict(
            t_mono=t_mono,
            source="synthetic",
            caps=_CAPS,
            packet_id=self._pid,
            on_track=True,
            speed_ms=speed,
            rpm=5000.0,
            rpm_max=8000.0,
            gear=3,
            throttle=0.5,
            brake=0.0,
            clutch=1.0,
            wheel_omega=_rolling(speed),
            tyre_radius_m=(_R, _R, _R, _R),
            load_n=(3000.0, 3000.0, 3200.0, 3200.0),
            susp_travel_m=(0.02, 0.02, 0.02, 0.02),
            tyre_temp_c=(80.0, 80.0, 82.0, 82.0),
            nd_slip=(0.4, 0.4, 0.4, 0.4),
            combined_slip=(1.0, 1.0, 1.0, 1.0),
            slip_ratio=(0.0, 0.0, 0.0, 0.0),
            slip_angle=(0.0, 0.0, 0.0, 0.0),
            accel_g=(0.0, 0.0, 0.0),
            surface_grip=1.0,
        )
        base.update(self._fn(t))
        return TelemetryFrame(**base)
