"""Assetto Corsa raw structs -> the normalized TelemetryFrame.

This is the ONLY module that knows AC's units, axis conventions and wheel order.
Assumptions still to confirm against a real capture are tagged ``# VERIFY``.
"""

from __future__ import annotations

import math

from openpulse.telemetry.ac import structs as S
from openpulse.telemetry.frame import Cap, TelemetryFrame, Wheel4

_PSI_TO_KPA = 6.894757
_KMH_TO_MS = 1.0 / 3.6

# AC shared-memory wheel arrays are already FL, FR, RL, RR — same as ours.


def _t4(arr) -> Wheel4:
    return (float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3]))


def _radius_speed(omega: Wheel4, radius: Wheel4) -> Wheel4:
    return tuple(  # type: ignore[return-value]
        (o * r if (math.isfinite(o) and math.isfinite(r) and r > 0) else math.nan)
        for o, r in zip(omega, radius, strict=True)
    )


def _all_zero(t: Wheel4) -> bool:
    return all(abs(x) < 1e-6 for x in t)


# --------------------------------------------------------------------------- #
#  shared memory
# --------------------------------------------------------------------------- #
def normalize_shm(
    p: S.ACPhysics, g: S.ACGraphics, st: S.ACStatic, *, t_mono: float
) -> TelemetryFrame:
    caps = (
        Cap.SUSP_TRAVEL_ABS
        | Cap.TYRE_TEMP
        | Cap.TYRE_PRESSURE
        | Cap.TYRE_WEAR
        | Cap.COMBINED_SLIP
        | Cap.WHEEL_OMEGA
        | Cap.OFF_TRACK_COUNT
        | Cap.TYRE_DIRT
        | Cap.DAMAGE
        | Cap.SURFACE_GRIP
        | Cap.LOCAL_VELOCITY
        | Cap.YAW_RATE
        | Cap.DRS
        | Cap.SESSION_STATE
    )
    load = _t4(p.wheelLoad)
    if not _all_zero(load):
        caps |= Cap.LOAD
    else:
        load = (math.nan,) * 4  # type: ignore[assignment]

    radius = _t4(st.tyreRadius)
    if not _all_zero(radius):
        caps |= Cap.TYRE_RADIUS
    else:
        radius = (math.nan,) * 4  # type: ignore[assignment]

    rpm_max = float(st.maxRpm)
    if rpm_max > 0:
        caps |= Cap.RPM_MAX

    omega = _t4(p.wheelAngularSpeed)
    accel_g = (float(p.accG[0]), float(p.accG[2]), float(p.accG[1]))  # (lat, lon, vert)  # VERIFY

    return TelemetryFrame(
        t_mono=t_mono,
        source="ac/shm",
        caps=caps,
        packet_id=int(p.packetId),
        on_track=(g.status == S.STATUS_LIVE),
        paused=(g.status == S.STATUS_PAUSE),
        in_pit=bool(g.isInPitLane or g.isInPit),
        surface_grip=float(g.surfaceGrip) if g.surfaceGrip > 0 else 1.0,
        lap=int(g.completedLaps),
        track_pos=float(g.normalizedCarPosition),
        speed_ms=float(p.speedKmh) * _KMH_TO_MS,
        rpm=float(p.rpms),
        rpm_max=rpm_max,
        gear=int(p.gear) - 1,  # AC: 0=R, 1=N, 2=1st
        throttle=float(p.gas),
        brake=float(p.brake),
        clutch=float(p.clutch),
        steer=float(p.steerAngle),  # VERIFY range (-1..1 vs radians)
        wheel_omega=omega,
        wheel_speed_ms=_radius_speed(omega, radius),
        combined_slip=_t4(p.wheelSlip),
        load_n=load,
        susp_travel_m=_t4(p.suspensionTravel),
        tyre_press_kpa=tuple(x * _PSI_TO_KPA for x in _t4(p.wheelsPressure)),  # type: ignore[arg-type]  # VERIFY
        tyre_temp_c=_t4(p.tyreCoreTemperature),
        tyre_wear=_t4(p.tyreWear),
        tyre_dirt=_t4(p.tyreDirtyLevel),
        tyre_radius_m=radius,
        accel_g=accel_g,
        local_vel_ms=(float(p.localVelocity[0]), float(p.localVelocity[1]), float(p.localVelocity[2])),
        yaw_rate=float(p.localAngularVelocity[1]),  # VERIFY axis
        abs_available=bool(p.abs > 0),
        tc_available=bool(p.tc > 0),
        drs_active=bool(p.drs > 0),
        off_track_wheels=int(p.numberOfTyresOut),
        damage=tuple(float(x) for x in p.carDamage),
    )


# --------------------------------------------------------------------------- #
#  UDP
# --------------------------------------------------------------------------- #
def normalize_udp(
    r: S.RTCarInfo, *, t_mono: float, packet_id: int, rpm_max_hint: float = 0.0
) -> TelemetryFrame:
    caps = (
        Cap.SLIP_RATIO
        | Cap.SLIP_ANGLE
        | Cap.COMBINED_SLIP
        | Cap.ND_SLIP
        | Cap.WHEEL_OMEGA
        | Cap.TYRE_RADIUS
        | Cap.LOAD
        | Cap.TYRE_DIRT
        | Cap.ABS_FLAG
        | Cap.TC_FLAG
        | Cap.ENGINE_LIMITER_FLAG
    )
    if rpm_max_hint > 0:
        caps |= Cap.RPM_MAX

    omega = _t4(r.wheelAngularSpeed)
    radius = _t4(r.tyreRadius)

    return TelemetryFrame(
        t_mono=t_mono,
        source="ac/udp",
        caps=caps,
        packet_id=packet_id,
        on_track=not bool(r.isInPit),   # UDP has no menu/replay/pause status
        paused=False,
        in_pit=bool(r.isInPit),
        speed_ms=float(r.speed_Ms),
        rpm=float(r.engineRPM),
        rpm_max=float(rpm_max_hint),
        gear=int(r.gear) - 1,  # VERIFY (assumed same as SHM: 0=R, 1=N)
        throttle=float(r.gas),
        brake=float(r.brake),
        clutch=float(r.clutch),
        steer=float(r.steer),
        wheel_omega=omega,
        wheel_speed_ms=_radius_speed(omega, radius),
        slip_ratio=_t4(r.slipRatio),
        slip_angle=_t4(r.slipAngle),
        combined_slip=_t4(r.tyreSlip),
        nd_slip=_t4(r.ndSlip),
        load_n=_t4(r.load),
        susp_travel_m=_t4(r.suspensionHeight),  # ride-height proxy, NOT absolute travel
        tyre_dirt=_t4(r.tyreDirtyLevel),
        tyre_radius_m=radius,
        accel_g=(float(r.accG_horizontal), float(r.accG_frontal), float(r.accG_vertical)),  # VERIFY
        abs_active=bool(r.isAbsInAction),
        tc_active=bool(r.isTcInAction),
        abs_available=bool(r.isAbsEnabled),
        tc_available=bool(r.isTcEnabled),
        engine_limiter=bool(r.isEngineLimiterOn),
        track_pos=float(r.carPositionNormalized),
    )
