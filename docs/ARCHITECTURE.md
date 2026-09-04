# OpenPulse — PC application (the "mixer")

> **Status: Phases A + B + C implemented** (transport + AC telemetry + effect
> engine — see §13). Phase A confirmed on the real box; B calibrated against a
> real capture; C v1 in place, feel-tuning still to come. This document is the
> target plan; it relies entirely on the firmware docs (separate repo, provided
> as reference): `OpenPulse-firmware/docs/` — `PROTOCOL.md`, `ARCHITECTURE.md`,
> `HARDWARE.md`, `ROADMAP.md`.
>
> **Decisions made**: "DMA" = AC's **official shared-memory interface**
> (`acpmf_*`). **Separate** repo from the firmware. **Multi-OS** (Windows +
> Linux + macOS) — explicit use case: box on one PC, game + UDP telemetry on
> another. Third-party dependencies **allowed** (numpy, pydantic, dearpygui,
> pyusb, libusb-package…).

---

## 0. In one sentence

A Python application (**dearpygui** GUI) that reads **telemetry from a game**,
turns it into a **single amplitude stream** through a modular effect engine,
and **streams it to the OpenPulse box** over protocol v1 (USB vendor bulk
today, 2.4 GHz later). First game: **Assetto Corsa** (shared memory *and*
UDP). The architecture is designed so other games can be added without
touching the effect engine or the transport.

---

## 1. What the firmware imposes (constraints, non-negotiable)

Pulled from the firmware docs. The app must follow these to the byte.

| Constraint | Detail | Consequence for the app |
|---|---|---|
| **The box is a dumb *renderer*** | No effect synthesis on board (just a self-test + a safety ramp). | **All** effect computation, mixing, shaping, and thermal protection happen on the PC. |
| **Input = amplitude envelope** | The box commutates the LRA open-loop at a fixed **~158.7 Hz**. The `int8` sample is *not* audio: it's the **amplitude** of that carrier. | Our effect "frequencies" are **amplitude-modulation** frequencies, useful band **~1–80 Hz** (beyond that the actuator can't keep up: 10–20 ms mechanical rise). |
| **Sample** | Signed `int8`, `-127..+127`. `0` = no drive. The PC normally sends `0..127`; negative = anti-phase (explicit braking). | v1: output `0..127`. Bipolar (a crisp impact edge) is a later option. |
| **Rate** | `sample_rate_hz` tunable `250..4000`, default **1000** (2000 OK). Bench: the box holds **≥ 4 kHz with no loss**. | DSP engine clocked at the device's `sample_rate_hz`. Firmware Phase 5 (PWM) not needed. |
| **Frame** | `[TYPE][SEQ][LEN][PAYLOAD≤56][CRC8]`, total 4..60 B. CRC-8/SMBUS (poly `0x07`). LE. **One frame = one USB bulk transaction.** | `batch ≤ 56` samples/frame. `proto.py` is a bit-identical mirror of `src/link/proto.h`. |
| **Handshake** | `RESYNC` → `PING` → `SET_CONFIG(version=1,…)` → `SET_MODE(SAMPLES)` → prime the FIFO → stream. `STATUS_REQ` at ~10 Hz. | **`RESYNC` first** (bench fix 2026-09-02: reseats SEQ, makes the box's local self-test step aside). |
| **Version** | `SET_CONFIG.version != 1` → `FAULT_VERSION` + data plane blocked. `PONG.proto_version` must be 1. | On connect: `PING`, check `proto_version` **and** `fw_major.fw_minor`; mismatch → refuse to stream + a clear message (PROTOCOL §7). |
| **Failsafe** | No valid data frame for `failsafe_ms` (default 100) → the box ramps to 0. USB reset / disconnect → immediate ramp. | When telemetry stops (pause, menu, game closed): ramp our output to 0 and keep sending zeros (avoids spamming `failsafe_trips`). |
| **Lookahead** | The PC keeps the box FIFO filled ~40 ms ahead (tunable). Box FIFO = 256 samples. | Tunable lookahead buffer **8–60 ms**, default ~20 ms. Latency/jitter trade-off exposed in the GUI. |
| **Loss** | Lossy-but-live. `loss_policy` 0 = zero-fill (default), 1 = hold-last. No ARQ. | Over USB loss is near zero; keep zero-fill. The choice will matter for RF (Phase 4). |
| **OD_CLAMP / OL_LRA_PERIOD** | **[MEASURED]** constants (`0xC0` ≈ 4.15 V peak; `64` ≈ 158.7 Hz). Do not change without a bench test. | The GUI exposes `od_clamp` behind an "Advanced (hardware)" panel with the HARDWARE.md warning; `ol_lra_period` is near read-only. "Volume" is set in **software** (amplitude scaling), not via `OD_CLAMP`. |
| **Thermal** | Salvaged actuator (Taptic Engine). Irreversible NdFeB demagnetization from ~80 °C. *Sustained* level `0x48/127` ≈ 2.35 V judged "thermally sustainable" on the bench. | **Software thermal guard mandatory**: energy integrator + gain foldback + reading `drv_status` OVER_TEMP / `FAULT_DRV_OT`. |
| **Windows** | No MS OS 2.0 descriptors (firmware Phase 3.6 not done) → interface 2 (vendor) needs a **WinUSB/libusbK bind via Zadig**, once. | The app detects "device seen but interface 2 not claimable" → a guided help screen. Goes away once 3.6 lands. |

**Direct reuse**: `OpenPulse-firmware/tools/pc_sender/` (`proto.py`,
`openpulse_send.py`) is a minimal sender **already bench-validated**. The
app's transport layer is a clean port of it; the pacing loop is reused as-is.

---

## 2. Guiding principles

1. **A clean boundary between telemetry ↔ effects ↔ transport.** Each layer
   has an abstract interface; adding a game = one `TelemetrySource` class +
   one normalizer, nothing else moves.
2. **A normalized, SI-unit, game-agnostic telemetry schema.** Effects never
   see an Assetto Corsa struct.
3. **The effect engine is a bank of small, independent DSP units.** Each is
   individually testable, can be enabled/disabled, and is tunable live.
4. **One actuator → one "vibratory language".** Every effect has a distinct
   signature (modulation band + texture) so it stays recognizable once
   everything is mixed together (§5.1).
5. **GUI and engine are separate.** dearpygui runs on its own thread; the
   engine on its own. They exchange snapshots + command queues. The GUI can
   never stall the pipeline.
6. **Everything is testable with no hardware.** `MockTransport` + replaying
   recorded telemetry → 100% Python CI, no game, no box.
7. **`proto.py` tracks `proto.h` to the byte.** Self-tests (struct sizes, CRC
   vector) fail loudly on drift.

**Non-goals for v1**: multi-actuator, steering-wheel force feedback / audio,
games other than Assetto Corsa, `ENVELOPE` mode, DataFlash persistence, NTC
thermal calibration, MS OS 2.0 descriptors (firmware side).

---

## 3. Pipeline overview

```
┌─ Thread T1 — Telemetry acquisition ────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ AC shared mem│  │  AC UDP:9996 │  │ (future game)│   concrete sources    │
│  │  acpmf_*     │  │  RTCarInfo   │  │              │                       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                       │
│         └───────────┬─────┴─────────────────┘                               │
│                     ▼ raw structs                                          │
│            ┌──────────────────┐                                            │
│            │ normalize (AC)   │──► TelemetryFrame  (SI, generic,           │
│            │ + derive (Δ, jerk)│      + CapabilitySet)                      │
│            └────────┬─────────┘                                            │
└─────────────────────┼──────────────────────────────────────────────────────┘
                      ▼  atomic swap of "latest frame"          + Recorder
┌─ Thread T2 — DSP + output  (perf_counter cadence) ─────────────────────────┐
│   ┌──────────────────────────────────────────────────────┐                 │
│   │ Pipeline.generate(n, fs)                              │                 │
│   │  road  engine  kerb  lockup  wheelspin  abs  shift …  │  each effect →  │
│   │   └──────┴──────┴──┬───┴──────────┴──────┴─────┘       │  0..1 vector   │
│   │                    ▼                                   │  @ fs, n samp. │
│   │        ┌────────────────────────┐                      │                │
│   │        │ Mixer + Master         │  beds vs events,     │                │
│   │        │  ducking / priority    │  soft limiter,       │                │
│   │        │  noise gate            │  thermal guard       │                │
│   │        └───────────┬────────────┘                      │                │
│   └────────────────────┼───────────────────────────────────┘                │
│                        ▼  float32 0..1 @ fs                                 │
│              ┌───────────────────┐                                         │
│              │ Renderer          │  int8 quantize · batch · lookahead      │
│              │                   │  buffer · real-time pacing              │
│              └────────┬──────────┘                                         │
│                       ▼  DATA_SAMPLES frames                                │
│              ┌───────────────────┐                                         │
│              │ Transport (USB)   │  proto v1 · EP2 OUT · handshake         │
│              └────────┬──────────┘                                         │
└───────────────────────┼────────────────────────────────────────────────────┘
                        ▼
                 ╔═════════════╗
                 ║  OpenPulse  ║   dumb firmware renderer
                 ║     box     ║
                 ╚═════════════╝
                        ▲  PONG / STATUS_REP / LOG / FAULT
┌─ Thread T3 — box feedback ─────────────────────────────────────────────────┐
│   parse → AppState.device (stats, version, faults, RTT)                     │
└────────────────────────────────────────────────────────────────────────────┘

   AppState  (60 Hz snapshots + command queues)  ◄──►  GUI thread (dearpygui)
```

---

## 4. The layers, one by one

### 4.1 Telemetry acquisition — `openpulse/telemetry/`

**Abstract interface** (`telemetry/base.py`):

```python
class TelemetrySource(Protocol):
    name: str
    def open(self) -> None: ...
    def close(self) -> None: ...
    def state(self) -> SourceState           # DISCONNECTED / WAITING_GAME / MENU / ON_TRACK
    def capabilities(self) -> CapabilitySet  # which fields this source can fill
    def poll(self, timeout_s: float) -> TelemetryFrame | None
    def session_info(self) -> SessionInfo | None   # game, car, track, max_rpm…
```

`registry.py`: discovery + auto-selection (tries shared memory, falls back to
UDP), or a manual choice in the GUI.

#### Assetto Corsa — shared memory (what you were calling "DMA")

> ⚠️ **Assumption to confirm**: "DMA" = AC's **official shared-memory
> interface** (named memory-mapped files). If you meant reading the process by
> pointer / a hardware DMA card instead, the acquisition layer changes — see
> §14 Q1.

- 3 Windows objects: `Local\acpmf_physics`, `Local\acpmf_graphics`,
  `Local\acpmf_static`, opened with `mmap.mmap(-1, size, tagname)`.
- Layout described as a `ctypes.Structure` in `telemetry/ac/structs.py`,
  transcribed **with the source cited** (AC's official Python SDK /
  `sdk/dev/`), the same way the firmware transcribes datasheets. Tolerance:
  fields appended at the *end* of the page depending on the game version → we
  read `min(len(buf), sizeof(struct))`.
- Rate: poll ~**333–500 Hz**, only consume when `physics.packetId` has
  changed (also detects dropped frames).
- Key fields used: `speedKmh`, `rpms`, `gear`, `gas`, `brake`, `clutch`,
  `steerAngle`, `accG[3]`, `wheelSlip[4]`, `wheelLoad[4]`,
  `wheelAngularSpeed[4]`, `suspensionTravel[4]`, `wheelsPressure[4]`,
  `tyreCoreTemperature[4]`, `slipRatio[4]`/`slipAngle[4]` (recent builds),
  `numberOfTyresOut`, `pitLimiterOn`, `carDamage[5]`, `drs`, `tc`, `abs`,
  `localAngularVel[3]`.
- `graphics.status` (OFF/REPLAY/LIVE/PAUSE), `isInPit`, `surfaceGrip`,
  `completedLaps`.
- `static.maxRpm`, `static.carModel`, `static.track` → `SessionInfo` +
  automatic profile selection.
- **Preferred source**: richer (suspension travel, tyre temps, damage,
  off-track wheels, `packetId`).

#### Assetto Corsa — UDP

- AC's UDP telemetry server listens on **port 9996** (localhost or a remote
  PC).
- Handshake: send `handshake{identifier, version, operationId}`: `0` =
  HANDSHAKE → `HandshakerResponse` reply (car/driver name, track); `1` =
  SUBSCRIBE_UPDATE → `RTCarInfo` stream; `3` = DISMISS on close.
- `RTCarInfo` (~328 B): `speed_Kmh`, `engineRPM`, `gear`, `gas`/`brake`/
  `clutch`, `steer`, `accG[3]`, `isAbsEnabled`, **`isAbsInAction`**,
  **`isTcInAction`**, `isInPit`, `isEngineLimiterOn`, `wheelAngularSpeed[4]`,
  `slipAngle[4]`, `slipRatio[4]`, `tyreSlip[4]`, `load[4]`,
  `tyreDirtyLevel[4]`, `suspensionHeight[4]`, `carCoordinates[3]`,
  `carPositionNormalized`, `carSlope`.
- **Missing vs shared memory**: `maxRpm` (→ a per-car default or a hint),
  `suspensionTravel` (we get `suspensionHeight` → derived),
  `tyreCoreTemperature`, `carDamage`, `numberOfTyresOut` (approximated from
  `tyreDirtyLevel`).
- **Gains**: `isAbsInAction` / `isTcInAction` natively (on shared memory we
  derive them).
- Reception: non-blocking socket + recv loop; ~90–100 Hz effective.
- Use case: game PC ≠ box PC, or shared memory unavailable.

`CapabilitySet` carries, per source, which fields are valid. An effect whose
input is missing: falls back to an estimator, or disables with a GUI note
("kerb: needs suspension travel → shared-memory source").

### 4.2 Normalization — `telemetry/frame.py`, `telemetry/derive.py`

`TelemetryFrame` (frozen, ~immutable dataclass) — SI units, wheel order
`[FL, FR, RL, RR]`:

| Group | Fields |
|---|---|
| meta | `t_mono` (s), `packet_id`, `dt` (s), `source`, `caps` |
| session | `on_track: bool`, `paused`, `in_pit`, `surface_grip` |
| vehicle | `speed_ms`, `rpm`, `rpm_max`, `gear` (int, −1=R 0=N), `throttle` 0..1, `brake` 0..1, `clutch` 0..1, `steer_rad` |
| wheels ×4 | `slip_ratio`, `slip_angle_rad`, `load_n`, `susp_travel_m`, `susp_vel_ms` *(derived)*, `wheel_speed_ms`, `tyre_press_kpa`, `tyre_temp_c`, `lock_ratio` *(derived)* |
| chassis | `accel_g` (x, y, z), `yaw_rate`, `jerk_g_s` *(derived)*, `ride_height_m` |
| flags | `abs_active`, `tc_active`, `abs_installed`, `tc_installed`, `drs`, `rev_limiter`, `engine_limiter_flag`, `off_track_wheels` (0..4) |
| events | `gear_changed` *(edge)*, `impact_g` *(derived, peak)*, `damage_delta` *(derived)* |

`derive.py` — cross-frame state (keeps a small sliding window):
- suspension velocity = filtered finite difference of `susp_travel_m` (or
  `susp_height` on UDP).
- `lock_ratio` = `wheel_speed / max(speed_ms, ε)` per wheel → lockup detection
  (< threshold under braking) / spin detection (> threshold under
  acceleration).
- `jerk_g_s` = derivative of `‖accel_g‖` → impact detection.
- `rev_limiter` = `rpm ≥ rpm_max − ε` **or** `engine_limiter_flag`.
- `gear_changed` = edge on `gear`.
- smoothing / anti-aliasing of noisy quantities (one-pole).

The AC normalizer (`telemetry/ac/normalize.py`) is **the only** module that
knows AC's units and frame of reference (km/h→m/s, wheel order, steer
sign…).

### 4.3 Effect engine — `openpulse/engine/`

**Interface** (`engine/effect.py`):

```python
@dataclass
class EffectContext:
    fs: int                     # = the device's sample_rate_hz
    t0: float                   # time of the block's first sample
    n: int                      # block size
    caps: CapabilitySet
    rng: np.random.Generator

class Effect(Protocol):
    id: str
    kind: Literal["bed", "event"]   # continuous bed | event-driven
    priority: int                    # arbitration / ducking
    def configure(self, params: EffectParams) -> None: ...
    def reset(self) -> None: ...
    def process(self, frame: TelemetryFrame, ctx: EffectContext) -> EffectBlock
```

`EffectBlock` = `np.ndarray float32 [n]` in `0..1` (amplitude contribution) +
an optional `duck: float` (dB of bed attenuation requested while the effect
is active). Each effect **keeps its phase continuous** across blocks
(oscillator phase accumulator, envelope state machine) and **smooths its
telemetry-derived parameters** (one-pole slew) to avoid *zipper noise*.

**DSP primitives** (`engine/dsp.py`, numpy, no global state):
`band_noise()` (filtered noise for textures), `Oscillator` (sin/tri/square/
pulse with duty cycle, phase-continuous), `OneShotEnv(attack, hold, decay)`
(triggerable), `OnePole` (smoothing / slew), `EdgeDetector` / `Schmitt`,
`SoftLimiter` (tanh knee), `NoiseGate` (hysteresis), `LeakyEnergy` (thermal
model).

**v1 catalogue (Assetto Corsa)** — each row = one module in `engine/effects/`:

| Effect | Kind | Inputs | Detection / mapping | Signature (band, texture) | GUI params |
|---|---|---|---|---|---|
| `road` | bed | `speed_ms`, HF content of `susp_travel` / `load_n` variance | HF suspension RMS × speed curve | smoothed broadband noise **5–40 Hz**, floor level | gain, speed curve, floor/ceiling |
| `engine_rpm` | bed | `rpm`, `rpm_max`, `throttle` | sub-harmonic of the rev count, mapped into **8–30 Hz**, rises with load | slow regular pulsation | gain, divider, load mix |
| `rev_limiter` | event | `rev_limiter` | flag / rpm threshold | **hard stutter ~7–10 Hz**, strong | threshold, gain, frequency |
| `gearshift` | event | `gear_changed`, `clutch` | edge on gear | **one 35–60 ms thump**, sharp attack (up ≠ down) | gain, duration, up/down |
| `kerb` | event | `susp_travel[axle]` oscillation, `load_n` spikes, `off_track_wheels` | correlated oscillation on one axle + partial off-track | modulated **~45–60 Hz burst**, intensity = oscillation amplitude | gain, threshold, frequency |
| `lockup` | event | front `lock_ratio`, `brake` | `lock_ratio < threshold` **and** `brake > threshold` | **slow ~12–18 Hz grind**, rough | slip threshold, gain, frequency |
| `wheelspin` | event | rear `lock_ratio`, `throttle`, `slip_ratio` | rear wheel over-rotation under acceleration | **fast ~25–35 Hz buzz** | threshold, gain, frequency |
| `grip_scrub` | bed | `grip_scrub` (derived from `nd_slip` / `combined_slip`) | tyres near the grip limit, without a hard lockup or slide | **fine ~20–28 Hz sizzle**, swells with saturation, front/rear separable | gain, knee (`scrub_nd_lo/hi`), front/rear mix |
| `abs_pulse` | event | `abs_active` (UDP) / derived (shared mem), `brake` | ABS-active flag | **regular ~15 Hz pulsation**, medium | gain, frequency, source |
| `tc_cut` | event | `tc_active` / derived slip | TC-active flag | **~20 Hz saccade**, light | gain, frequency |
| `impact` | event | `jerk_g_s`, `impact_g`, `damage_delta` | `jerk` above threshold or new damage | **big 40–120 ms transient**, max attack, intensity = `\|jerk\|` | threshold, gain, curve, cooldown |
| `offtrack` | event | `off_track_wheels` / `tyre_dirty`, `speed_ms` | number of wheels off × speed | **coarse noisy rumble ~8–20 Hz** | gain, speed mix, per wheel |
| `slip_slide` *(optional)* | bed | rear `slip_angle`, `yaw_rate` | the rear progressively steps out | **rising 10–20 Hz tremor** | gain, threshold, on/off |

Adding an effect = drop a file into `engine/effects/`, register it, add its
param block to the profile schema. Zero impact elsewhere.

### 4.4 Mixer & master — `engine/mixer.py`

Per-block processing order:

1. **Beds**: sum, scaled so the combined bed ≤ `bed_ceiling` (default ~0.5).
2. **Events**: sum. Each can request **ducking** of the bed (X dB
   attenuation for its duration, short release).
3. **Priority arbitration**: if two high-priority events overlap (`impact` +
   `kerb`), the lower-priority one is **attenuated, not dropped** — you still
   feel the kerb under the impact. Order: `impact` > `kerb` >
   `lockup`/`wheelspin` > `abs`/`tc` > `gearshift` > `rev_limiter` > `engine`
   > `road`.
4. **Master gain**.
5. **Soft limiter** (tanh knee) at 1.0 — the sum of coincident effects never
   clips hard.
6. **Noise gate**: below `gate_floor` (≈ 0.02) for `gate_hold` → hard **0**
   output (protects the actuator, avoids idle buzz). Hysteresis so it
   doesn't chatter.
7. **Thermal guard** (`engine/thermal.py`):
   - `E ← E·decay + Σ sample²·dt` (leaky integrator, time constant ~tens of s
     to a few minutes — cf. HARDWARE §4, the actuator bolted to a bracket =
     heatsink).
   - `E > soft_threshold` → `master *= foldback(E)` (soft curve).
   - `drv_status` OVER_TEMP bit **or** `FAULT_DRV_OT` received → output
     forced to 0 + a GUI alarm until acknowledged.
   - Constants = conservative estimates (target sustained level around
     `0x48/127`), tunable; calibratable later against the NTC (firmware
     Phase 6.3).

Output: `float32 [n]` in `0..1`.

### 4.5 Renderer / device output — `openpulse/output/renderer.py`

- **Internal fs = the device's `sample_rate_hz`** (default 1000, ≤ 2000). No
  resampling in v1. (If a fixed internal fs independent of the device is ever
  needed: a polyphase resampler, isolated here.)
- The pipeline produces **blocks** of ~8–16 samples (2–16 ms).
- **Quantization**: `int8(clip(round(x·127), 0, 127))`. Optional ~0.5 LSB TPDF
  dither (smooths slow ramps) — GUI toggle.
- **Batch + frames**: `batch` samples per `DATA_SAMPLES` (default 8, ≤ 56).
- **Lookahead buffer**: keep `lookahead_ms` ahead (default ~20 ms, 8–60).
  Primed before streaming, exactly like `openpulse_send.py`.
- **Pacing** (reused as-is from the reference sender):
  `target = int((now − t0)·fs) + lookahead ; while i < target: send(batch)`.
- **`STATUS_REQ`** at 10 Hz (emitted here or by T3).
- **Stale telemetry** (no frame for > `stale_ms`, or `on_track` false, or
  `paused`) → target envelope 0, keep sending zeros (avoids needless
  `failsafe_trips`). Auto-resumes on the next frame.
- **Box `amp_max` left at 127**: all shaping happens in software (more
  resolution, smoother). `od_clamp` stays the analog ceiling.

### 4.6 Transport — `openpulse/transport/`

```python
class Transport(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def send(self, frame: bytes) -> None
    def frames_in(self) -> Iterator[bytes]      # received frames, already deframed
    def stats(self) -> TransportStats
```

| Impl. | Role |
|---|---|
| `UsbVendorTransport` | pyusb, claims **interface 2** (leaves CDC to the OS), EP2 OUT/IN. Port of `open_box()` + a `PONG`/`STATUS`/`LOG`/`FAULT` reader thread. `handshake()` helper (RESYNC→PING→SET_CONFIG→SET_MODE) + version check on the `PONG`. |
| `MockTransport` | In-process fake box: decodes `DATA_SAMPLES` → a ring buffer the GUI plots (and/or a `.wav`), synthesizes plausible `STATUS_REP`. Runs **the whole app + GUI with no hardware**. |
| *(future)* `RfDongleTransport` | Firmware Phase 4, same interface. |

`transport/protocol.py` = a **bit-identical** port of
`OpenPulse-firmware/tools/pc_sender/proto.py` (framing, CRC-8, struct
packing) + the self-test `assert`s (`_CONFIG.size == 12`, `_STATS.size ==
40`, `crc8("123456789") == 0xF4`). Documented sync rule: *any change to
`src/link/proto.h` is mirrored here in the same commit* (same rule as the
firmware's `tools/`).

---

## 5. Single-actuator haptic design

### 5.1 The "vibratory language"

One LRA → no way to spatially separate "front-left kerb" from "rear lockup".
Effects are told apart by their **texture**:

| Effect | Modulation band | Character |
|---|---|---|
| road | 5–40 Hz, noisy | smooth floor, "the car is rolling" |
| engine | 8–30 Hz, periodic | slow hum |
| abs | ~15 Hz, regular | clean beat |
| lockup | 12–18 Hz, rough | a dragging growl |
| wheelspin | 25–35 Hz, buzzing | a high-pitched flutter |
| grip_scrub | 20–28 Hz, lightly grainy | a sizzle that swells toward the limit |
| tc | ~20 Hz | brief micro-saccade |
| kerb | 45–60 Hz, hard | crackle |
| rev-limiter | 7–10 Hz, hard | a hard-hitting stop |
| gearshift | 40 ms transient | one sharp hit |
| impact | 40–120 ms transient | a hit |

A **default mix table** (relative levels) and the priority/ducking order ship
in the `ac-default` profile and are documented in `docs/EFFECTS.md`. The user
tunes to taste.

### 5.2 Latency budget (PC side)

| Step | Typical cost | Note |
|---|---|---|
| Shared-memory poll | 2–3 ms | bounded by the poll interval (333–500 Hz) |
| UDP reception | ~10 ms | network/OS jitter, worse than shared memory |
| Telemetry ZOH → DSP | ≤ 1 block (2–16 ms) | |
| DSP generation of one block | ≪ 1 ms | numpy |
| **Lookahead buffer** | **8–60 ms (default ~20)** | **the dominant knob**, jitter ↔ latency trade-off |
| USB `dev.write()` | ~0.1 ms | measured on the firmware: 88 µs |
| Firmware pipeline (frame → playable) | ~1.7 ms | measured, BENCH.md |
| LRA mechanical rise | 10–20 ms | irreducible |

→ Total felt latency ≈ **35–55 ms** on shared memory / 20 ms lookahead, about
half of which is mechanical + the tunable buffer. Windows:
`timeBeginPeriod(1)` at startup for a 1 ms `sleep`.

---

## 6. Orchestration & threads — `openpulse/core/`

| Thread | Role | Cadence |
|---|---|---|
| **T1 acquisition** | `source.poll()` → normalize → `state.telemetry.set(frame)` (atomic swap); feeds the `Recorder` | shared mem ~500 Hz / UDP on arrival |
| **T2 DSP + output** | reads the latest frame (ZOH), applies pending GUI commands, `pipeline.generate()`, `renderer` → `transport.send()` | tight `perf_counter` loop, 2–16 ms blocks |
| **T3 box feedback** | `transport.frames_in()` → parse → `state.device` (stats, PONG, faults), log | blocking + timeout |
| **GUI** (main) | dearpygui, manual render loop; reads snapshots, pushes commands | ~60 fps |

**State exchange** (`core/state.py`):
- `state.telemetry`: the latest `TelemetryFrame` (swapped reference, lock-free
  read).
- `state.commands`: a `queue.Queue` of GUI → T2 commands (gain change, effect
  toggle, device `SET_CONFIG`, profile change…).
- `state.snapshot`: a `Snapshot` (dataclass) published at ~60 Hz by T2/T3,
  read by the GUI. Contains: displayable telemetry, per-effect meters, output
  waveform (a ~2 s ring), device stats, RTT, faults.
- **All** dearpygui mutations happen on the GUI thread. Workers never touch a
  widget.

**Clean shutdown**: signal → T2 sends `SET_MODE(IDLE)` → `transport.close()`
(release the interface) → join the threads (daemon). `timeEndPeriod(1)`.

Why threads + queues and not asyncio: pyusb is blocking, dearpygui has its
own loop, and the reference sender's pacing pattern (already bench-validated)
is synchronous. Threads + `queue` are simpler and proven here.

---

## 7. Configuration & profiles — `openpulse/config/`

- **pydantic** models: `AppSettings` (VID/PID, transport type, UDP host/port,
  UI prefs), `DeviceConfig` (`sample_rate_hz`, `od_clamp`, `ol_lra_period`,
  `failsafe_ms`, `amp_max`, `loss_policy`, `underrun_decay_ms`),
  `MixerConfig`, `EffectParams` per effect, `Profile` (name, game, car-match
  pattern, `device`, `mixer`, `effects{}`).
- Storage: `%APPDATA%/OpenPulse/` (`settings.toml`, `profiles/*.toml`).
  Bundled default profiles, copied on first launch (`ac-default`, `ac-gt3`,
  `ac-formula`, …).
- **Profile resolution**: on telemetry connect, read `carModel` (shared
  memory) / `carName` (UDP handshake) → the profile whose pattern
  (glob/regex) matches, else the game's default. Manual override in the GUI.
  `Save` / `Save as` / `Duplicate` / `Revert`.
- **Device guard rails**: clamp to the protocol's ranges (`link_control.c`).
  *Warn* (without blocking) if `od_clamp > 0xC0` (thermal) or
  `sample_rate_hz > 2000` (unneeded, BENCH.md). Show the frequency for
  `ol_lra_period` (`f = 1e6/(olp·98.46)`) and the peak voltage for
  `od_clamp` (`≈ 21.6 mV·od_clamp`) in plain terms. `ol_lra_period` behind an
  "Advanced (hardware)" panel with the HARDWARE.md warning — it's a measured
  constant.

---

## 8. dearpygui GUI — `openpulse/gui/`

Manual render loop:
`while dpg.is_dearpygui_running(): pull_snapshot(); update_widgets(); dpg.render_dearpygui_frame()`.
Panels (dockable windows):

1. **Connection** — device (status, `fw x.y`, `proto vN` + ✓/✗, current
   mode), transport (USB / Mock), telemetry source (Auto / Shared memory /
   UDP + host:port), game + car + track status. Connect / Disconnect /
   Rescan buttons. A Zadig help screen if interface 2 isn't claimable.
2. **Live monitor** — a scope of the output waveform (~2 s) + an overlay of
   per-effect contributions; speed/rpm/gear readouts, throttle/brake bars, 4
   slip gauges, G vector, flag chips; device stats (fifo, ovr/unr, crc, gap,
   backlog, `failsafe_trips`, VBAT, OVER_TEMP/OC in red, RTT).
3. **Effects** — a table: `[✓]` name | gain | threshold(s) | freq | curve |
   live contribution meter | **Test** button. Grouped bed / event. "Mute
   all", presets.
4. **Mixer / Master** — master gain, bed ceiling, limiter (knee, ceiling),
   noise gate (floor, hold, hysteresis), thermal guard (time constant,
   threshold, foldback), a big output VU + a clip indicator + a "thermal
   budget" gauge.
5. **Device config** — `sample_rate_hz`, `failsafe_ms`, `amp_max`,
   `loss_policy`, `underrun_decay_ms`; an Advanced panel: `od_clamp` (+ ~V),
   `ol_lra_period` (+ ~Hz, warning). An **Apply** button → `SET_CONFIG`
   (optional: reset stats). A visual diff between the app and the box.
6. **Profiles** — list, load/save/duplicate, car association,
   import/export, auto-switch on/off.
7. **Diagnostics** — box log (`LOG` color-coded by level), decoded `FAULT`s,
   protocol counters, a mini-bench (PING RTT n=100, "send a test pattern",
   "force IDLE"), a raw-frame trace toggle, **telemetry
   recording/replay** (start/stop/load + timeline).

---

## 9. Recording / replay & simulator mode

- `telemetry/record.py`: `TelemetryRecorder` writes `TelemetryFrame`s (`.npz`
  or msgpack columns) + metadata (game, car, session). `TelemetryPlayer`
  **is a `TelemetrySource`** that replays a file in real time (or ×k, or on
  loop).
- `SyntheticSource`: a `TelemetrySource` driven by sliders / scripted
  scenarios ("hotlap", "lock the fronts", "hit a kerb", "spin out") → tune
  and demo the effects **with no game**.
- `tools/telemetry_capture.py`: dumps a live AC session (both sources) to a
  recording, for test fixtures.
- `tools/waveform_preview.py`: runs a recording through the pipeline offline
  → `.wav` + PNG, for A/B tuning and regression checks.

---

## 10. Tests (no hardware, no game) — `tests/`

| File | Covers |
|---|---|
| `test_protocol.py` | CRC vector `"123456789"→0xF4`, struct sizes (2/5/12/40/5), build/parse round-trip, malformed frames rejected, `config_msg` packing (mirrors the intent of `firmware/tools/test/`) |
| `test_ac_shared_memory.py` / `test_ac_udp.py` | parsing fixture dumps → raw fields, endianness, tolerance to fields appended at the tail |
| `test_normalize.py` | raw AC (both sources) → equivalent `TelemetryFrame`s on a shared fixture session; unit conversions; `CapabilitySet` |
| `test_derive.py` | suspension velocity, `lock_ratio`, impact/jerk detection, `gear_changed` on synthetic sequences |
| `test_effects.py` | per effect: bounded `0..1`; silent when disabled; silent when the trigger is absent; RMS/peak in the expected band when triggered; dominant modulation frequency (FFT) within the declared band; phase continuity across blocks (no jump) |
| `test_mixer.py` | limiter never > 1.0; gate engages/releases with hysteresis; ducking attenuates the bed; thermal foldback reduces the master under sustained load; OVER_TEMP → 0 |
| `test_renderer.py` | quantization, batch sizes, lookahead maintained, pacing math, stale telemetry → ramp to 0 |
| `test_pipeline_integration.py` | `MockTransport` + a recorded lap → N s → no exceptions, frame cadence within tolerance, SEQ monotonic, bounded output |

CI: pure-Python `pytest`. Fixtures committed under `tests/fixtures/`.

---

## 11. Proposed layout

> Phase A shipped `transport/` (`protocol.py`, `base.py`, `usb.py`,
> `mock.py`), `box/session.py`, `core/clock.py`, `streamer.py`,
> `controller.py`, `config.py`, `waveforms.py`, `gui/app.py`, `cli.py`,
> `app.py`. Everything below is the target.

```
OpenPulse-app/
├── pyproject.toml                   Python ≥ 3.11
├── README.md
├── docs/
│   ├── ARCHITECTURE.md              (this document)
│   ├── EFFECTS.md                   the vibratory language + catalogue + tuning
│   ├── TELEMETRY.md                 AC shared-mem + UDP field maps, normalized schema
│   └── SETUP-WINDOWS.md             libusb / Zadig / WinUSB
├── src/openpulse/
│   ├── app.py                       entrypoint: wires everything, owns the threads
│   ├── core/       state.py  clock.py  logging.py
│   ├── config/     models.py  store.py  defaults/*.toml
│   ├── telemetry/
│   │   ├── base.py  frame.py  registry.py  derive.py  record.py
│   │   └── ac/      shared_memory.py  udp.py  structs.py  normalize.py
│   ├── engine/
│   │   ├── effect.py  dsp.py  mixer.py  pipeline.py
│   │   └── effects/ road.py kerb.py lockup.py wheelspin.py abs_pulse.py
│   │                tc_cut.py gearshift.py engine_rpm.py rev_limiter.py
│   │                impact.py offtrack.py
│   ├── output/     renderer.py  thermal.py
│   ├── transport/  base.py  protocol.py  usb_vendor.py  mock.py
│   └── gui/        main_window.py  panels/*.py  widgets/*.py
├── tests/          fixtures/  test_*.py
└── tools/          telemetry_capture.py  waveform_preview.py
```

Location: `OpenPulse-app/` next to `OpenPulse-firmware/`. Same repo or a
separate one — decided (§14 Q3): separate.

---

## 12. Packaging & platform

- Deps: `dearpygui`, `pyusb`, `numpy`, `pydantic`, `msgpack` (or stdlib),
  `tomllib`. Dev: `pytest`, `ruff`, `mypy`.
- **Windows** (the main sim-racing target): bundle `libusb-1.0.dll`. Bind
  **WinUSB/libusbK via Zadig on interface 2**, once — guided by the app,
  documented. Goes away once firmware 3.6 (MS OS 2.0) lands.
- The CDC port (`COMx`, `[R]` lines) is *optional*: `TYPE_LOG` + `STATUS_REP`
  already cover the need. It can be opened read-only for a diagnostics tab.
- Distribution: `pyinstaller` one-folder → `OpenPulse.exe` (not phase 1).
- **Linux / macOS**: `libusb-package` provides the backend; on Linux a udev
  rule may be needed to claim the interface without privileges. AC mostly
  runs on Windows — the Linux/macOS case targets the "box" PC that receives
  **UDP telemetry** from another PC (shared memory assumes the game is
  local; under Proton it lives inside the wine prefix).

---

## 13. Roadmap (shippable increments)

Same style as the firmware ROADMAP: every phase compiles, runs, and has a
"done" criterion.

### Phase A — Skeleton + transport (parity with `openpulse_send.py`)  —  ✅ DONE
- `transport/protocol.py` (bit-identical mirror of `proto.h` + self-tests),
  `transport/base.py` (`Transport` ABC), `transport/usb.py`
  (`UsbVendorTransport` + `libusb-package` backend), `transport/mock.py`
  (`MockTransport`, a behavioural model of the box).
- `box/session.py` (`BoxSession`: handshake, version gate, reader thread,
  RTT), `core/clock.py` (OS timer resolution + `Pacer`), `streamer.py`
  (`SampleStreamer`, the real-time pacing loop), `config.py` (`DeviceConfig`
  pydantic model matching `link_control.c`'s ranges), `waveforms.py`.
- `openpulse-cli` — headless streamer, parity with `openpulse_send.py`
  (`--transport usb|mock`, `--wave`, `--list`, dashboard, Ctrl-C → IDLE).
- dearpygui GUI (`gui/app.py`, `controller.py`): **Connection** panel
  (transport, VID/PID, `sample_rate_hz`/`failsafe_ms`, waveform, start/stop)
  + **Diagnostics** panel (STATUS table, RTT min/p50/p95, decoded FAULTs, box
  log, Ping×50 button).
- **Tests** (`pytest`, 100% no hardware): `test_protocol`, `test_mock`,
  `test_session`, `test_streamer`, `test_controller`, `test_gui_smoke`.
- **Done**: box (real or mock) driven from the app, live dashboard, version
  check on `PONG` (`ProtocolMismatch`), clean shutdown (`SET_MODE IDLE`).

### Phase B — Assetto Corsa telemetry  —  ✅ DONE (calibrated against a real capture)
- `telemetry/frame.py` (frozen `TelemetryFrame`, ~50 SI fields + `Cap`
  IntFlag), `telemetry/base.py` (`TelemetrySource` ABC, `SessionInfo`,
  `SourceStatus`).
- `telemetry/ac/structs.py` (ctypes, ported from mdjarv (shared memory
  `Pack=4`) + rickwest (UDP `RTCarInfo` 328 B) — sizes/offsets asserted),
  `ac/shared_memory.py` (mmap of `acpmf_*`, Windows), `ac/udp.py` (handshake
  on :9996 + `SUBSCRIBE_UPDATE`, multi-OS), `ac/normalize.py` (the only
  module that knows AC's units; assumptions tagged `# VERIFY`).
- `telemetry/derive.py` (`Deriver`: dt, `lock_ratio`, `susp_vel`,
  jerk/impact, `gear_change`, `rev_limiter`, ABS/TC estimation,
  **`grip_scrub`**).
- `telemetry/synthetic.py` (`SyntheticSource`: 6 scenarios including
  `grip_scrub`), `telemetry/record.py` (`TelemetryRecorder` JSONL +
  `ReplaySource`), `telemetry/registry.py` (`build_source` + SHM→UDP
  auto-detection), `telemetry/controller.py` (`TelemetryController`: thread,
  snapshot, recording).
- `tools/`: `openpulse-capture` (dump + sanity report + `--raw`),
  `openpulse-analyze` (percentiles of a capture → threshold calibration),
  `openpulse-trim` (big capture → a small committable fixture),
  `openpulse-bridge` + `debugmap.py` (**debug bridge**: streams one
  telemetry scalar — `grip_scrub`, `lockup`, `rpm`… — straight to the box).
- GUI: **Telemetry** window (source, live monitor: speed, rpm, 4 wheels × 9
  metrics, `grip_scrub` as a bar, flags; recording; debug bridge to the
  box).
- Tests: `test_ac_structs`, `test_normalize`, `test_derive`,
  `test_synthetic`, `test_record`, `test_telemetry_controller`,
  `test_ac_fixture` (runs against `tests/fixtures/ac_shm_lap.jsonl`, a
  trimmed real GT capture).
- **Calibrated** against a real shared-memory capture (40 s, GT): AC units
  and axes confirmed (see [TELEMETRY.md](TELEMETRY.md)), `impact_excess_g`
  and `scrub_wheelslip_*` tuned to the observed percentiles. **Still open**:
  the same pass on the UDP side (the `gear` offset), the yaw index of
  `localAngularVelocity`.

### Phase C — Effect engine v1  —  ✅ DONE (v1, feel-tuning still to come)
- `engine/effect.py` (`Effect` ABC + `EffectContext`/`EffectBlock`),
  `engine/dsp.py` (phase-continuous osc, band noise, one-shot envelope,
  hysteretic gate, soft limiter, energy integrator), `engine/params.py`
  (pydantic models per effect + mixer, edited live from the GUI).
- **12 effects** in `engine/effects/`: `road`, `engine`, `grip_scrub`,
  `lockup`, `wheelspin`, `abs_pulse`, `tc_cut`, `kerb`, `gearshift`,
  `rev_limiter`, `impact`, `offtrack` (see [EFFECTS.md](EFFECTS.md)).
- `engine/mixer.py`: ambient bed (duckable) + foreground bed (`grip_scrub`)
  + events → ducking → master → soft limiter → noise gate → thermal guard
  (leaky energy + `external_limit` from the box's `OVER_TEMP`/
  `FAULT_DRV_OT`) → output gamma.
- `engine/pipeline.py`: latest frame (ZOH) → per-sample effect rendering →
  mix → one 0..1 block; `enable` ramps to 0 on stale telemetry / off track.
- `streamer.PipelineStreamer`: block → int8 quantize → box, same pacing as
  Phase A.
- `tools/preview.py` (`openpulse-preview`): replays a capture through the
  engine offline → WAV (envelope × 158.7 Hz carrier) + a per-effect report.
- GUI: **Effects** window (per effect: on/off, gain, auto-generated params,
  meter, Test button) and **Mixer / output** window (mixer params + VU +
  scope); an "Effect engine" button in the Telemetry window.
- Tests: `test_dsp`, `test_effects` (per effect: bounded, silent when off /
  trigger absent, present for the right scenario), `test_mixer`,
  `test_pipeline` (scenarios + the real `ac_shm_lap.jsonl` lap). 120 tests.
- **Calibrated** against `ac_shm_lap.jsonl`: `openpulse-preview` → RMS ≈
  0.37 (below `thermal_soft`), transients ~1.0, `tc_cut`/`kerb`/`wheelspin`
  brought down to realistic activation rates. Left: the feel-tuning pass on
  the box (including the `grip_scrub` slope).

### Phase D — Profiles + thermal guard + polish
- `thermal.py`, final limiter/gate, per-car profiles (auto-switch on
  `carModel`), a **Device config** panel (`SET_CONFIG` + guard rails),
  PyInstaller packaging, `docs/SETUP-WINDOWS.md`.
- **Done**: real use across several cars, thermally safe, installable by a
  third party.

### Phase E — Extensibility (validate the abstraction)
- A 2nd game (e.g. ACC, or rFactor 2 / iRacing) → proves that only a
  `TelemetrySource` + a normalizer are needed.
- SimHub as an alternative telemetry source.
- `ENVELOPE` mode (useful for the future RF, firmware Phase 4).
- Anticipate multi-actuator (the protocol would gain a channel byte, the
  FIFO would become per-channel — already noted in the firmware ROADMAP's
  "Deferred" section).

---

## 14. Open questions

**Decided** (see header): "DMA" = AC's official shared memory · separate
repo · multi-OS (Windows/Linux/macOS) · third-party libs OK.

**Still to settle before Phase C** (now resolved in practice — kept here for
history):

1. **v1 effect scope**: does the §4.3 catalogue work for you? Priorities if
   some need cutting?
2. **Negative samples (anti-phase)**: allowed for a crisp impact edge, or
   strictly `0..127` in v1? — *decided: `0..127` for v1.*
3. **Single actuator**: nothing planned for multi-channel for now (just
   don't close the door)?
4. **SimHub**: kept in Phase E as *one source among others*, or a priority? —
   *decided: Phase E.*
5. **`proto` sync**: with the two repos separate, syncing
   `transport/protocol.py` ↔ `firmware/src/link/proto.h` is manual
   (documented rule + size/CRC self-tests). Good enough, or do we want a
   verification script that points at a firmware checkout?
