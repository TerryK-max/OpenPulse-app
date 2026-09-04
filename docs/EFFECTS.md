# Effects — the vibratory language

The box drives one LRA at a fixed ~158.7 Hz carrier; the app streams its
**amplitude envelope** (int8, 0..127). So every effect outputs an
amplitude-modulation signal in **0..1**, and its "frequency" is a modulation
frequency. Useful band ≈ **1–40 Hz** — the actuator's ~10–20 ms rise time
mushes anything faster.

One actuator can't place "front-left kerb" apart from "rear lockup" spatially, so
effects are distinguished by **texture** (modulation band + character). Keep the
defaults in that spirit when tuning.

| effect | kind | modulation | character | driven by |
|---|---|---|---|---|
| `road` | bed (duckable) | 3–30 Hz noise | smooth floor, "the car is rolling" | `speed_ms`, suspension roughness |
| `engine` | bed (duckable) | 5–22 Hz throb, rises with revs | low hum | `rpm/rpm_max`, `throttle` |
| `grip_scrub` | bed | 14–26 Hz granular sizzle | swells toward the limit | Deriver `grip_scrub_front/rear` |
| `lockup` | event | ~13 Hz rough grind | drags under braking | front `lock_ratio` < thr + `brake` |
| `wheelspin` | event | ~23 Hz buzz | rear over-rotation | rear `lock_ratio` > thr + `throttle` |
| `abs_pulse` | event | ~14 Hz square pump | regular pedal pumping | `abs_active` + `brake` |
| `tc_cut` | event | ~16 Hz stutter | light saccade | `tc_active` + `throttle` |
| `kerb` | event | elevated + ~38 Hz crunch | sharp burst | axle `susp_vel` spike, `off_track_wheels` |
| `gearshift` | event | 40 ms one-shot | one sharp thump (up ≠ down) | `gear_change` edge |
| `rev_limiter` | event | ~9 Hz hard square | limiter bounce | `rev_limiter` |
| `impact` | event | 40–120 ms one-shot | a hit | `impact_g` rising edge, `damage` delta |
| `offtrack` | event | 8–22 Hz coarse noise | grass/gravel rumble | `off_track_wheels` (SHM) / `tyre_dirt` (UDP) |

## Mixer

```
ambient beds (road, engine)  ──duck──▶  Σ  ──▶ master ──▶ soft limiter ──▶ gate ──▶ thermal ──▶ ^curve ──▶ 0..1
foreground beds (grip_scrub) ───────────▲                                                    │
events                        ──duck req─┘                                        external_limit (box OVER_TEMP)
```

- **duck**: events (and `grip_scrub` as it swells) attenuate the ambient bed in
  dB so they punch through.
- **soft limiter** (`limiter_knee`): the sum never clips hard.
- **noise gate** (`gate_open`/`gate_close`/`gate_hold_ms`, hysteretic): forces a
  hard 0 below threshold — no idle hum, protects the actuator.
- **thermal guard** (`thermal_tau_s` ~45 s leaky energy, `thermal_soft`/`_hard`
  mean-square): folds the master back over ~tens of seconds if the output sits
  high. `thermal_soft=0.18` ≈ RMS 0.42 ≈ the HARDWARE.md sustained-safe level.
  `FAULT_DRV_OT` / STATUS `OVER_TEMP` from the box → `external_limit=0`.
- `enable` ramps 0→1 on track, →0 when telemetry goes stale or you leave the
  track (`stale_ms`, `enable_ramp_ms`).

## Tuning

Every param is a live pydantic field the GUI edits (Effects / Mixer panels) —
changes apply without restarting the stream. Workflow:

1. Capture a lap: `openpulse-capture --source ac-shm --out recordings/lap.jsonl`.
2. See where the signals sit: `openpulse-analyze recordings/lap.jsonl --on-track-only`.
3. Render it through the engine and listen: `openpulse-preview recordings/lap.jsonl`
   → a WAV of the envelope on the 158.7 Hz carrier + a per-effect report
   (peak, % of the lap active).
4. Adjust params; re-run `openpulse-preview --config settings.json` to A/B.

Defaults were calibrated against one real GT lap (`tests/fixtures/ac_shm_lap.jsonl`):
RMS ≈ 0.37 (below the thermal-soft threshold), transients to ~1.0. The **#1 feel
knob** is `grip_scrub` — widen `in_lo..in_hi` / lower `curve` for a gentler,
earlier swell.
