"""AC ctypes layouts — sizes and key offsets vs the documented references."""

import ctypes

from openpulse.telemetry.ac import structs as S


def test_udp_sizes():
    assert ctypes.sizeof(S.RTCarInfo) == 328
    assert ctypes.sizeof(S.HandshakerResponse) == 408
    assert ctypes.sizeof(S.HandshakeRequest) == 12


def test_rtcarinfo_offsets():
    # offsets documented by rickwest/ac-remote-telemetry-client
    want = {
        "identifier": 0, "size": 4, "speed_Kmh": 8, "speed_Ms": 16,
        "isAbsEnabled": 20, "isEngineLimiterOn": 25, "accG_vertical": 28,
        "lapTime": 40, "gas": 56, "gear": 76, "cgHeight": 80,
        "wheelAngularSpeed": 84, "slipAngle": 100, "slipRatio": 132,
        "tyreSlip": 148, "ndSlip": 164, "load": 180, "suspensionHeight": 292,
        "carPositionNormalized": 308, "carCoordinates": 316,
    }
    for name, off in want.items():
        assert getattr(S.RTCarInfo, name).offset == off, name


def test_shm_sizes_match_reference():
    # mdjarv/assettocorsasharedmemory (Pack=4)
    assert ctypes.sizeof(S.ACPhysics) == 580
    assert ctypes.sizeof(S.ACGraphics) == 288
    assert ctypes.sizeof(S.ACStatic) == 648


def test_shm_key_offsets():
    assert S.ACPhysics.packetId.offset == 0
    assert S.ACPhysics.gear.offset == 16
    assert S.ACGraphics.status.offset == 4
    assert S.ACStatic.maxRpm.offset > 0


def test_from_buffer_copy_roundtrip():
    r = S.RTCarInfo()
    r.identifier = b"a"
    r.size = 328
    r.speed_Ms = 42.5
    r.gear = 3
    r.ndSlip[:] = [0.9, 0.9, 0.8, 0.8]
    raw = bytes(r)
    assert len(raw) == 328
    r2 = S.RTCarInfo.from_buffer_copy(raw)
    assert r2.speed_Ms == 42.5 and r2.gear == 3 and abs(r2.ndSlip[0] - 0.9) < 1e-6
