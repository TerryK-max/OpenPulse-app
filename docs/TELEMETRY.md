# Telemetry — Assetto Corsa

How AC's two telemetry interfaces map onto the normalized
[`TelemetryFrame`](../src/openpulse/telemetry/frame.py). Both are implemented;
the pipeline (Phase C) will pick one via `CapabilitySet` and degrade effects
whose inputs a source cannot provide.

## Sources

| | `ac-shm` (`ACSharedMemorySource`) | `ac-udp` (`ACUDPSource`) |
|---|---|---|
| transport | 3 named memory-mapped sections (`Local\acpmf_*`) | UDP :9996, handshake + `SUBSCRIBE_UPDATE` |
| OS | **Windows only** | any (game may be on another PC) |
| rate | physics rate, polled 333–500 Hz on `packetId` | ~physics rate, one packet in |
| struct source | [mdjarv/assettocorsasharedmemory](https://github.com/mdjarv/assettocorsasharedmemory) (`Pack=4`) | [rickwest/ac-remote-telemetry-client](https://github.com/rickwest/ac-remote-telemetry-client) (328-byte `RTCarInfo`) |

`auto` tries shared memory first (richer), falls back to UDP.

## What each source fills

| normalized field | `ac-shm` | `ac-udp` |
|---|---|---|
| `speed_ms` | `speedKmh`/3.6 | `speed_Ms` |
| `rpm` / `rpm_max` | `rpms` / `static.maxRpm` | `engineRPM` / **hint only** (UDP has no maxRpm) |
| `gear` | `gear − 1` | `gear − 1` *(VERIFY)* |
| `throttle` `brake` `clutch` `steer` | `gas` `brake` `clutch` `steerAngle` | `gas` `brake` `clutch` `steer` |
| `wheel_omega` | `wheelAngularSpeed[4]` | `wheelAngularSpeed[4]` |
| `wheel_speed_ms` | `omega × static.tyreRadius` | `omega × tyreRadius` |
| `slip_ratio` `slip_angle` | — (not in base AC SHM) | `slipRatio[4]` `slipAngle[4]` |
| `combined_slip` | `wheelSlip[4]` | `tyreSlip[4]` |
| `nd_slip` | — | `ndSlip[4]` |
| `load_n` | `wheelLoad[4]` *(0 on recent builds → cap off)* | `load[4]` |
| `susp_travel_m` | `suspensionTravel[4]` (real travel, `SUSP_TRAVEL_ABS`) | `suspensionHeight[4]` (ride-height proxy, no cap) |
| `tyre_temp_c` `tyre_press_kpa` `tyre_wear` | `tyreCoreTemperature` `wheelsPressure×6.895` `tyreWear` | — |
| `tyre_dirt` | `tyreDirtyLevel[4]` | `tyreDirtyLevel[4]` |
| `accel_g` | `(accG[0], accG[2], accG[1])` = (lat, lon, vert) *(VERIFY axes)* | `(horizontal, frontal, vertical)` |
| `yaw_rate` | `localAngularVelocity[1]` *(VERIFY axis)* | — |
| `abs_active` `tc_active` | derived (`Deriver`) | `isAbsInAction` `isTcInAction` (`ABS_FLAG`/`TC_FLAG`) |
| `engine_limiter` | derived from `rpm≈rpm_max` | `isEngineLimiterOn` |
| `off_track_wheels` | `numberOfTyresOut` | — |
| `damage` | `carDamage[5]` | — |
| `surface_grip` | `graphics.surfaceGrip` | — |
| `on_track` / `paused` | `graphics.status` (LIVE/PAUSE) | `not isInPit` (no menu/replay/pause status) |

## Derived (`Deriver`, `derive.py`)

`dt`, `wheel_speed_ms`/`tyre_radius_m` (fallback radius), `lock_ratio`
(`wheel_speed / car_speed`; <1 locking, >1 spinning), `susp_vel_ms` (filtered
finite difference), `jerk_g_s` + `impact_g` (peak/hold on |accel|), `gear_change`
(one-frame edge), `rev_limiter`, `abs_active`/`tc_active` estimates when the
source has no flag, and **`grip_scrub`** / `grip_scrub_front` / `grip_scrub_rear`.

### `grip_scrub` — the "tyres at the limit" cue

The signal for *tyres scrubbing / squealing near the friction limit* — distinct
from `lockup` (front wheels ~stopped under braking) and `wheelspin` (rear wheels
over-rotating). Computed from `nd_slip` (UDP, ~1.0 = limit) via a smoothstep
around `scrub_nd_lo..hi`; on shared memory, from `combined_slip` (AC `wheelSlip`,
unbounded) via `scrub_wheelslip_lo..hi`. Those bounds are **first guesses** —
calibrate them against a real capture (`DeriveConfig`, tunable in the GUI later).
Phase C adds a dedicated `grip_scrub` effect; Phase B exposes the number and the
debug bridge can already stream it to the box (`--map grip_scrub`).

## Verifying a capture

`# VERIFY` tags in `normalize.py` mark assumptions to confirm once, against a
real session — see [tests/fixtures/README.md](../tests/fixtures/README.md).
Priorities: `gear` offset (UDP), `accel_g` axis order & signs, `wheelsPressure`
unit (psi assumed), `steerAngle` range (SHM), `localAngularVelocity` yaw index.
