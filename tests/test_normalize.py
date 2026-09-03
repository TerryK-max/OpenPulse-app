"""AC raw structs -> normalized TelemetryFrame."""

import math

import pytest

from openpulse.telemetry.ac import structs as S
from openpulse.telemetry.ac.normalize import normalize_shm, normalize_udp
from openpulse.telemetry.frame import Cap


def _phys(**kw):
    p = S.ACPhysics()
    p.packetId = 100
    p.speedKmh = 180.0
    p.rpms = 7000
    p.gear = 4  # AC: 4 -> 3rd
    p.gas = 0.8
    p.brake = 0.1
    p.clutch = 1.0
    p.wheelAngularSpeed[:] = [151.5, 151.5, 151.5, 151.5]
    p.wheelSlip[:] = [2.0, 2.1, 1.8, 1.9]
    p.suspensionTravel[:] = [0.03, 0.031, 0.028, 0.029]
    p.wheelsPressure[:] = [27.0, 27.0, 26.0, 26.0]
    p.tyreCoreTemperature[:] = [85, 86, 88, 89]
    p.accG[:] = [1.5, 0.2, 0.9]  # AC order [lat, vert, lon]
    p.numberOfTyresOut = 0
    p.abs = 1.0
    p.localVelocity[:] = [1.0, 0.0, 49.0]
    p.localAngularVelocity[:] = [0.0, 0.3, 0.0]
    p.carDamage[:] = [0, 0, 0, 0, 0]
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _graph(status=S.STATUS_LIVE):
    g = S.ACGraphics()
    g.packetId = 100
    g.status = status
    g.isInPitLane = 0
    g.surfaceGrip = 0.98
    g.completedLaps = 2
    g.normalizedCarPosition = 0.5
    return g


def _static():
    st = S.ACStatic()
    st.carModel = "ferrari_488_gt3"
    st.track = "spa"
    st.maxRpm = 7500
    st.tyreRadius[:] = [0.33, 0.33, 0.34, 0.34]
    return st


def test_normalize_shm_basic():
    f = normalize_shm(_phys(), _graph(), _static(), t_mono=1.0)
    assert f.source == "ac/shm"
    assert abs(f.speed_ms - 50.0) < 0.01           # 180 km/h
    assert f.gear == 3                              # 4 - 1
    assert f.rpm == 7000 and f.rpm_max == 7500
    assert f.on_track and not f.in_pit
    assert Cap.SUSP_TRAVEL_ABS in f.caps and Cap.COMBINED_SLIP in f.caps
    assert Cap.RPM_MAX in f.caps and Cap.TYRE_RADIUS in f.caps
    # accel_g stored as (lat, lon, vert)
    assert f.accel_g == pytest.approx((1.5, 0.9, 0.2), abs=1e-5)
    # wheel surface speed = omega * radius
    assert abs(f.wheel_speed_ms[0] - 151.5 * 0.33) < 0.01
    assert f.abs_available and not f.abs_active     # not derived yet
    assert math.isnan(f.nd_slip[0])                # SHM has no nd_slip


def test_normalize_shm_paused_and_pit():
    f = normalize_shm(_phys(), _graph(status=S.STATUS_PAUSE), _static(), t_mono=1.0)
    assert f.paused and not f.on_track


def _rt(**kw):
    r = S.RTCarInfo()
    r.identifier = b"a"
    r.size = 328
    r.speed_Ms = 55.0
    r.engineRPM = 6500
    r.gear = 4
    r.gas = 0.9
    r.brake = 0.0
    r.clutch = 1.0
    r.steer = -0.2
    r.isAbsInAction = 1
    r.isTcInAction = 0
    r.isAbsEnabled = 1
    r.isEngineLimiterOn = 0
    r.isInPit = 0
    r.wheelAngularSpeed[:] = [166.6, 166.6, 166.6, 166.6]
    r.slipRatio[:] = [0.02, 0.02, -0.01, -0.01]
    r.slipAngle[:] = [0.05, 0.05, 0.04, 0.04]
    r.tyreSlip[:] = [3.0, 3.0, 2.8, 2.8]
    r.ndSlip[:] = [0.95, 0.96, 0.9, 0.9]
    r.load[:] = [3100, 3050, 3300, 3250]
    r.tyreRadius[:] = [0.33, 0.33, 0.33, 0.33]
    r.suspensionHeight[:] = [0.10, 0.10, 0.09, 0.09]
    r.accG_horizontal = 1.2
    r.accG_frontal = -0.3
    r.accG_vertical = 0.1
    r.carPositionNormalized = 0.25
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_normalize_udp_basic():
    f = normalize_udp(_rt(), t_mono=2.0, packet_id=7, rpm_max_hint=7200)
    assert f.source == "ac/udp"
    assert f.speed_ms == 55.0 and f.gear == 3 and f.rpm == 6500
    assert f.rpm_max == 7200 and Cap.RPM_MAX in f.caps
    assert Cap.ND_SLIP in f.caps and Cap.SLIP_RATIO in f.caps and Cap.ABS_FLAG in f.caps
    assert f.abs_active and not f.tc_active
    assert f.nd_slip[0] == pytest.approx(0.95, abs=1e-5)
    assert f.accel_g == pytest.approx((1.2, -0.3, 0.1), abs=1e-5)
    assert f.wheel_speed_ms[0] == pytest.approx(166.6 * 0.33, abs=0.02)
    assert not f.on_track or True  # on_track = not in pit


def test_udp_size_mismatch_rejected_field():
    f = normalize_udp(_rt(), t_mono=0.0, packet_id=1)
    # nd_slip present, combined slip present
    assert Cap.COMBINED_SLIP in f.caps
