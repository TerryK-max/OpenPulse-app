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
| `gear` | `gear − 1` (0=R, 1=N, 2=1st — confirmed) | `gear − 1` *(assumed same; VERIFY once with a UDP capture)* |
| `throttle` `brake` `clutch` `steer` | `gas` `brake` `clutch` `steerAngle` | `gas` `brake` `clutch` `steer` |
| `wheel_omega` | `wheelAngularSpeed[4]` | `wheelAngularSpeed[4]` |
| `wheel_speed_ms` | `omega × static.tyreRadius` | `omega × tyreRadius` |
| `slip_ratio` `slip_angle` | — (not in base AC SHM) | `slipRatio[4]` `slipAngle[4]` |
| `combined_slip` | `wheelSlip[4]` | `tyreSlip[4]` |
| `nd_slip` | — | `ndSlip[4]` |
| `load_n` | `wheelLoad[4]` *(0 on recent builds → cap off)* | `load[4]` |
| `susp_travel_m` | `suspensionTravel[4]` (real travel, `SUSP_TRAVEL_ABS`) | `suspensionHeight[4]` (ride-height proxy, no cap) |
| `tyre_temp_c` `tyre_press_kpa` `tyre_wear` | `tyreCoreTemperature` · `wheelsPressure×6.895` (psi→kPa, confirmed ≈205 kPa) · `(100−tyreWear)/100` (0=new) | — |
| `tyre_dirt` | `min(1, tyreDirtyLevel/5)` (AC counter runs 0..~5) | same |
| `accel_g` | `(accG[0], accG[2], accG[1])` = (lat, lon, vert) — AC is X-lat/Y-vert/Z-lon, confirmed via `localVelocity[2]`==fwd speed | `(horizontal, frontal, vertical)` |
| `yaw_rate` | `localAngularVelocity[1]` *(axis unconfirmed — reads ~0 straight)* | — |
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
around `scrub_nd_lo..hi`; on shared memory, from `combined_slip` (AC `wheelSlip`)
via `scrub_wheelslip_lo..hi`.

**Calibrated** against a real 40 s GT lap (`combined_slip.max` p50≈0.74,
p90≈1.8, p95≈2.9, p99≈8.4): defaults `scrub_wheelslip_lo=0.8`, `hi=3.5` →
`grip_scrub` is ~0 for the routine ~75 % of a lap, swells to ~0.3–0.7 through the
hard corners, and only pegs to 1.0 in a genuine slide. This is the **#1 knob to
tune by feel** (`DeriveConfig`, GUI in Phase C). Use `openpulse-analyze
<capture>` to see the distribution for a given car.

## Verifying a capture

`# VERIFY` tags in `normalize.py` mark the remaining unconfirmed assumptions.
Confirmed on a shared-memory capture (2026-09): `speedKmh`/`rpms`/`gear−1`,
`steerAngle` is normalized −1..1, `wheelsPressure` is psi, `accG` axes
(X-lat/Y-vert/Z-lon), `wheelAngularSpeed` rad/s + `tyreRadius` m, `wheelLoad`
non-zero on this build, `surfaceGrip` reachable. **Still open**: the UDP `gear`
offset (assumed same), `localAngularVelocity` yaw index, whether `accG` includes
the 1 g gravity term. See [tests/fixtures/README.md](../tests/fixtures/README.md).
