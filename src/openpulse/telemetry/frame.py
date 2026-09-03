"""The normalized, game-agnostic telemetry schema.

SI units. Per-wheel quantities are 4-tuples in the order ``FL, FR, RL, RR``.
A field that a source cannot provide is left at ``nan`` (floats) / its default,
and the corresponding :class:`Cap` bit is cleared so effects can degrade or
disable cleanly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntFlag, auto

WHEELS = ("FL", "FR", "RL", "RR")

Wheel4 = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

NAN = math.nan
_NAN4: Wheel4 = (NAN, NAN, NAN, NAN)
_ZERO3: Vec3 = (0.0, 0.0, 0.0)


class Cap(IntFlag):
    """Which fields a source actually fills. Effects check these."""

    NONE = 0
    SUSP_TRAVEL_ABS = auto()   # susp_travel_m is real travel (not a ride-height proxy)
    TYRE_TEMP = auto()
    TYRE_PRESSURE = auto()
    TYRE_WEAR = auto()
    LOAD = auto()
    SLIP_RATIO = auto()
    SLIP_ANGLE = auto()
    COMBINED_SLIP = auto()     # AC WheelSlip / UDP tyreSlip
    ND_SLIP = auto()           # normalized slip, ~1.0 = grip limit
    WHEEL_OMEGA = auto()
    TYRE_RADIUS = auto()
    ABS_FLAG = auto()          # abs_active is reported natively (not derived)
    TC_FLAG = auto()
    ENGINE_LIMITER_FLAG = auto()
    RPM_MAX = auto()
    OFF_TRACK_COUNT = auto()
    TYRE_DIRT = auto()
    DAMAGE = auto()
    SURFACE_GRIP = auto()
    LOCAL_VELOCITY = auto()
    YAW_RATE = auto()
    DRS = auto()
    SESSION_STATE = auto()     # on_track / paused are meaningful (not just "packets flowing")


@dataclass(frozen=True, slots=True)
class TelemetryFrame:
    # -- meta --
    t_mono: float                 # perf_counter() at capture
    source: str                   # "ac/shm" | "ac/udp" | "replay" | "synthetic"
    caps: Cap = Cap.NONE
    packet_id: int = 0            # monotonic id from the game (or a synthesized counter)
    dt: float = 0.0              # s since previous frame (0 = unknown, filled by Deriver)

    # -- session --
    on_track: bool = False       # physics live, driver in control
    paused: bool = False
    in_pit: bool = False
    surface_grip: float = 1.0
    lap: int = 0
    track_pos: float = 0.0       # normalized 0..1

    # -- vehicle --
    speed_ms: float = 0.0
    rpm: float = 0.0
    rpm_max: float = 0.0         # 0 = unknown
    gear: int = 0               # -1 = R, 0 = N, 1..n
    throttle: float = 0.0       # 0..1
    brake: float = 0.0          # 0..1
    clutch: float = 1.0         # 0..1, 1 = fully engaged (drive connected)
    steer: float = 0.0          # -1..1

    # -- per wheel [FL, FR, RL, RR] --
    wheel_omega: Wheel4 = _NAN4        # rad/s
    wheel_speed_ms: Wheel4 = _NAN4     # contact-patch surface speed (omega * radius)
    slip_ratio: Wheel4 = _NAN4         # longitudinal, <0 locking, >0 spinning
    slip_angle: Wheel4 = _NAN4         # rad
    combined_slip: Wheel4 = _NAN4      # AC WheelSlip / UDP tyreSlip (unbounded-ish)
    nd_slip: Wheel4 = _NAN4            # normalized, ~1 = at the grip limit
    load_n: Wheel4 = _NAN4
    susp_travel_m: Wheel4 = _NAN4      # real travel (SHM) or ride-height proxy (UDP)
    tyre_press_kpa: Wheel4 = _NAN4
    tyre_temp_c: Wheel4 = _NAN4
    tyre_wear: Wheel4 = _NAN4
    tyre_dirt: Wheel4 = _NAN4          # 0..1
    tyre_radius_m: Wheel4 = _NAN4

    # -- chassis --
    accel_g: Vec3 = _ZERO3            # (lateral +right, longitudinal +accel, vertical +up)  [VERIFY axes]
    local_vel_ms: Vec3 = (NAN, NAN, NAN)
    yaw_rate: float = NAN             # rad/s

    # -- flags --
    abs_active: bool = False
    tc_active: bool = False
    abs_available: bool = False
    tc_available: bool = False
    drs_active: bool = False
    engine_limiter: bool = False      # native pit/rev limiter flag if the source has one
    off_track_wheels: int = 0
    damage: tuple[float, ...] = ()

    # -- derived (Deriver.process fills these; nan / 0 = not computed) --
    susp_vel_ms: Wheel4 = _NAN4
    lock_ratio: Wheel4 = _NAN4        # wheel_speed / car_speed; <1 locking, >1 spinning
    grip_scrub: float = NAN           # 0..~1.5 — tyres near/at the friction limit ("scrub")
    grip_scrub_front: float = NAN
    grip_scrub_rear: float = NAN
    jerk_g_s: float = NAN
    impact_g: float = 0.0            # >0 on a detected hit, decays
    gear_change: int = 0            # -1 downshift, 0 none, +1 upshift (single frame)
    rev_limiter: bool = False

    # -- convenience --
    def wheel(self, name: str, attr: str) -> float:
        return getattr(self, attr)[WHEELS.index(name)]

    @property
    def speed_kmh(self) -> float:
        return self.speed_ms * 3.6
