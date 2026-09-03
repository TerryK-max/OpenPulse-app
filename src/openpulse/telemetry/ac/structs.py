"""ctypes layouts for the Assetto Corsa telemetry interfaces.

Shared memory (``Local\\acpmf_*``): ported field-for-field from the widely-used
C# reference https://github.com/mdjarv/assettocorsasharedmemory
(``[StructLayout(Pack=4, CharSet=Unicode)]``). AC over-allocates each mapped
region, so we map and parse only the prefix defined here — newer builds / CSP
append fields we do not read.

UDP (port 9996, ``RTCarInfo`` / ``HandshakerResponse``): ported from
https://github.com/rickwest/ac-remote-telemetry-client with the byte offsets it
documents. ``RTCarInfo.size`` (a field in the packet) is asserted against
``sizeof`` at runtime.

Field names are kept close to the game's own so they can be cross-checked against
either reference. Unit/axis assumptions that still need confirming against a real
capture are tagged ``# VERIFY``.
"""

from __future__ import annotations

import ctypes

# --------------------------------------------------------------------------- #
#  Shared memory  (wchar_t strings, Pack = 4)
# --------------------------------------------------------------------------- #
PHYSICS_TAG = "Local\\acpmf_physics"
GRAPHICS_TAG = "Local\\acpmf_graphics"
STATIC_TAG = "Local\\acpmf_static"

_f = ctypes.c_float
_i = ctypes.c_int32


class ACPhysics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", _i),
        ("gas", _f),
        ("brake", _f),
        ("fuel", _f),
        ("gear", _i),
        ("rpms", _i),
        ("steerAngle", _f),
        ("speedKmh", _f),
        ("velocity", _f * 3),
        ("accG", _f * 3),
        ("wheelSlip", _f * 4),
        ("wheelLoad", _f * 4),           # 0 on some recent builds
        ("wheelsPressure", _f * 4),      # psi  # VERIFY
        ("wheelAngularSpeed", _f * 4),   # rad/s
        ("tyreWear", _f * 4),
        ("tyreDirtyLevel", _f * 4),
        ("tyreCoreTemperature", _f * 4),
        ("camberRAD", _f * 4),
        ("suspensionTravel", _f * 4),    # m
        ("drs", _f),
        ("tc", _f),
        ("heading", _f),
        ("pitch", _f),
        ("roll", _f),
        ("cgHeight", _f),
        ("carDamage", _f * 5),
        ("numberOfTyresOut", _i),
        ("pitLimiterOn", _i),
        ("abs", _f),
        ("kersCharge", _f),
        ("kersInput", _f),
        ("autoShifterOn", _i),
        ("rideHeight", _f * 2),
        ("turboBoost", _f),
        ("ballast", _f),
        ("airDensity", _f),
        ("airTemp", _f),
        ("roadTemp", _f),
        ("localAngularVelocity", _f * 3),  # rad/s, [pitch, yaw, roll]  # VERIFY axis order
        ("finalFF", _f),
        ("performanceMeter", _f),
        ("engineBrake", _i),
        ("ersRecoveryLevel", _i),
        ("ersPowerLevel", _i),
        ("ersHeatCharging", _i),
        ("ersIsCharging", _i),
        ("kersCurrentKJ", _f),
        ("drsAvailable", _i),
        ("drsEnabled", _i),
        ("brakeTemp", _f * 4),
        ("clutch", _f),                  # 1.0 = fully engaged
        ("tyreTempI", _f * 4),
        ("tyreTempM", _f * 4),
        ("tyreTempO", _f * 4),
        ("isAIControlled", _i),
        ("tyreContactPoint", (_f * 3) * 4),
        ("tyreContactNormal", (_f * 3) * 4),
        ("tyreContactHeading", (_f * 3) * 4),
        ("brakeBias", _f),
        ("localVelocity", _f * 3),
    ]


class ACGraphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", _i),
        ("status", _i),        # 0 OFF, 1 REPLAY, 2 LIVE, 3 PAUSE
        ("session", _i),       # -1 unknown, 0 practice, 1 qualify, 2 race, 3 hotlap, ...
        ("currentTime", ctypes.c_wchar * 15),
        ("lastTime", ctypes.c_wchar * 15),
        ("bestTime", ctypes.c_wchar * 15),
        ("split", ctypes.c_wchar * 15),
        ("completedLaps", _i),
        ("position", _i),
        ("iCurrentTime", _i),
        ("iLastTime", _i),
        ("iBestTime", _i),
        ("sessionTimeLeft", _f),
        ("distanceTraveled", _f),
        ("isInPit", _i),
        ("currentSectorIndex", _i),
        ("lastSectorTime", _i),
        ("numberOfLaps", _i),
        ("tyreCompound", ctypes.c_wchar * 33),
        ("replayTimeMultiplier", _f),
        ("normalizedCarPosition", _f),
        ("carCoordinates", _f * 3),
        ("penaltyTime", _f),
        ("flag", _i),
        ("idealLineOn", _i),
        ("isInPitLane", _i),
        ("surfaceGrip", _f),
        ("mandatoryPitDone", _i),
    ]


class ACStatic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", ctypes.c_wchar * 15),
        ("acVersion", ctypes.c_wchar * 15),
        ("numberOfSessions", _i),
        ("numCars", _i),
        ("carModel", ctypes.c_wchar * 33),
        ("track", ctypes.c_wchar * 33),
        ("playerName", ctypes.c_wchar * 33),
        ("playerSurname", ctypes.c_wchar * 33),
        ("playerNick", ctypes.c_wchar * 33),
        ("sectorCount", _i),
        ("maxTorque", _f),
        ("maxPower", _f),
        ("maxRpm", _i),
        ("maxFuel", _f),
        ("suspensionMaxTravel", _f * 4),
        ("tyreRadius", _f * 4),
        ("maxTurboBoost", _f),
        ("deprecated1", _f),
        ("deprecated2", _f),
        ("penaltiesEnabled", _i),
        ("aidFuelRate", _f),
        ("aidTireRate", _f),
        ("aidMechanicalDamage", _f),
        ("aidAllowTyreBlankets", _i),
        ("aidStability", _f),
        ("aidAutoClutch", _i),
        ("aidAutoBlip", _i),
        ("hasDRS", _i),
        ("hasERS", _i),
        ("hasKERS", _i),
        ("kersMaxJoules", _f),
        ("engineBrakeSettingsCount", _i),
        ("ersPowerControllerCount", _i),
        ("trackSPlineLength", _f),
        ("trackConfiguration", ctypes.c_wchar * 15),
        ("ersMaxJ", _f),
        ("isTimedRace", _i),
        ("hasExtraLap", _i),
        ("carSkin", ctypes.c_wchar * 33),
        ("reversedGridPositions", _i),
        ("pitWindowStart", _i),
        ("pitWindowEnd", _i),
    ]


# AC_STATUS
STATUS_OFF, STATUS_REPLAY, STATUS_LIVE, STATUS_PAUSE = 0, 1, 2, 3


# --------------------------------------------------------------------------- #
#  UDP  (port 9996)
# --------------------------------------------------------------------------- #
UDP_PORT = 9996

# handshake operationId
OP_HANDSHAKE = 0
OP_SUBSCRIBE_UPDATE = 1
OP_SUBSCRIBE_SPOT = 2
OP_DISMISS = 3


class HandshakeRequest(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("identifier", _i),   # platform id, unused (send 1)
        ("version", _i),      # protocol version, unused (send 1)
        ("operationId", _i),
    ]


class HandshakerResponse(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("carName", ctypes.c_wchar * 50),
        ("driverName", ctypes.c_wchar * 50),
        ("identifier", _i),
        ("version", _i),
        ("trackName", ctypes.c_wchar * 50),
        ("trackConfig", ctypes.c_wchar * 50),
    ]


class RTCarInfo(ctypes.Structure):
    """One physics-step telemetry packet (identifier byte 'a', 328 bytes)."""

    # natural alignment matches the game's layout: 3 pad bytes after `identifier`,
    # 2 pad bytes after the six int8 flags.
    _fields_ = [
        ("identifier", ctypes.c_char * 4),   # "a\0"
        ("size", _i),
        ("speed_Kmh", _f),
        ("speed_Mph", _f),
        ("speed_Ms", _f),
        ("isAbsEnabled", ctypes.c_uint8),
        ("isAbsInAction", ctypes.c_uint8),
        ("isTcInAction", ctypes.c_uint8),
        ("isTcEnabled", ctypes.c_uint8),
        ("isInPit", ctypes.c_uint8),
        ("isEngineLimiterOn", ctypes.c_uint8),
        ("accG_vertical", _f),
        ("accG_horizontal", _f),
        ("accG_frontal", _f),
        ("lapTime", _i),
        ("lastLap", _i),
        ("bestLap", _i),
        ("lapCount", _i),
        ("gas", _f),
        ("brake", _f),
        ("clutch", _f),
        ("engineRPM", _f),
        ("steer", _f),
        ("gear", _i),
        ("cgHeight", _f),
        ("wheelAngularSpeed", _f * 4),
        ("slipAngle", _f * 4),
        ("slipAngle_ContactPatch", _f * 4),
        ("slipRatio", _f * 4),
        ("tyreSlip", _f * 4),
        ("ndSlip", _f * 4),
        ("load", _f * 4),
        ("Dy", _f * 4),
        ("Mz", _f * 4),
        ("tyreDirtyLevel", _f * 4),
        ("camberRAD", _f * 4),
        ("tyreRadius", _f * 4),
        ("tyreLoadedRadius", _f * 4),
        ("suspensionHeight", _f * 4),
        ("carPositionNormalized", _f),
        ("carSlope", _f),
        ("carCoordinates", _f * 3),
    ]


RTCARINFO_SIZE = ctypes.sizeof(RTCarInfo)
RTLAP_SIZE = 212
HANDSHAKE_RESPONSE_SIZE = ctypes.sizeof(HandshakerResponse)

# self-tests — the UDP layout is well-pinned; assert it
assert RTCARINFO_SIZE == 328, f"RTCarInfo is {RTCARINFO_SIZE}, expected 328"
assert HANDSHAKE_RESPONSE_SIZE == 408, f"HandshakerResponse is {HANDSHAKE_RESPONSE_SIZE}, expected 408"
assert ctypes.sizeof(HandshakeRequest) == 12

# the SHM sizes below are self-consistency checks against the mdjarv reference;
# the real mapped region is larger (CSP / newer builds append fields).
assert ctypes.sizeof(ACPhysics) == 580, ctypes.sizeof(ACPhysics)
assert ctypes.sizeof(ACGraphics) == 288, ctypes.sizeof(ACGraphics)
assert ctypes.sizeof(ACStatic) == 648, ctypes.sizeof(ACStatic)
