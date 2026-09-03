"""Pydantic parameter models for every effect + the mixer + the engine.

The GUI mutates these in place; the running Pipeline holds the same instances and
reads current values each block, so tweaks apply live without losing DSP state.
Defaults are first-pass and meant to be tuned by feel.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EffectBase(_Base):
    enabled: bool = True
    gain: float = Field(1.0, ge=0.0, le=4.0)


# --------------------------------------------------------------------------- #
#  beds
# --------------------------------------------------------------------------- #
class RoadParams(EffectBase):
    speed_min_ms: float = 3.0
    speed_ref_ms: float = 35.0
    curve: float = Field(1.3, gt=0)
    susp_gain: float = Field(0.6, ge=0)
    susp_ref_ms: float = 0.35        # |susp_vel| that counts as "rough"
    noise_lo_hz: float = 3.0
    noise_hi_hz: float = 30.0
    ceiling: float = Field(0.22, ge=0, le=1)


class EngineRpmParams(EffectBase):
    freq_lo_hz: float = 5.0
    freq_hi_hz: float = 22.0
    floor: float = Field(0.03, ge=0, le=1)
    throttle_gain: float = Field(0.10, ge=0)
    rpm_gain: float = Field(0.06, ge=0)
    ceiling: float = Field(0.22, ge=0, le=1)
    shape: str = "tri"


class GripScrubParams(EffectBase):
    front_mix: float = Field(1.0, ge=0, le=1)
    rear_mix: float = Field(0.8, ge=0, le=1)
    in_lo: float = Field(0.05, ge=0, le=1)      # remap of the derived grip_scrub 0..1
    in_hi: float = Field(0.95, ge=0, le=1)
    curve: float = Field(1.0, gt=0)             # <1 = swells earlier / gentler
    noise_lo_hz: float = 14.0
    noise_hi_hz: float = 26.0
    am_hz: float = 20.0                          # faint amplitude modulation on the sizzle
    am_depth: float = Field(0.25, ge=0, le=1)
    duck_db: float = Field(3.0, ge=0, le=12)     # ducks the ambient road/engine bed as it swells
    ceiling: float = Field(0.5, ge=0, le=1)


# --------------------------------------------------------------------------- #
#  events
# --------------------------------------------------------------------------- #
class LockupParams(EffectBase):
    brake_min: float = 0.15
    lock_ratio_thr: float = 0.85     # front wheel speed / car speed below this = locking
    lock_ratio_range: float = 0.5
    freq_hz: float = 13.0
    rough: float = Field(0.5, ge=0, le=1)
    level: float = Field(0.85, ge=0, le=1)
    release_ms: float = 90.0
    duck_db: float = 4.0


class WheelspinParams(EffectBase):
    throttle_min: float = 0.2
    lock_ratio_thr: float = 1.14     # rear wheel speed / car speed above this = spinning
    lock_ratio_range: float = 0.7
    freq_hz: float = 23.0
    rough: float = Field(0.35, ge=0, le=1)
    level: float = Field(0.62, ge=0, le=1)
    release_ms: float = 70.0
    duck_db: float = 3.0


class AbsPulseParams(EffectBase):
    brake_min: float = 0.2
    freq_hz: float = 14.0
    duty: float = Field(0.5, gt=0, lt=1)
    level: float = Field(0.6, ge=0, le=1)
    duck_db: float = 2.5


class TcCutParams(EffectBase):
    throttle_min: float = 0.35
    freq_hz: float = 16.0
    duty: float = Field(0.4, gt=0, lt=1)
    level: float = Field(0.32, ge=0, le=1)
    duck_db: float = 2.0


class KerbParams(EffectBase):
    susp_vel_thr: float = 0.40       # m/s of |susp_vel| on an axle (real lap: p90≈0.26, p95≈0.44)
    susp_vel_range: float = 1.4
    off_track_boost: float = 0.18
    texture_hz: float = 38.0
    texture_depth: float = Field(0.5, ge=0, le=1)
    level: float = Field(0.78, ge=0, le=1)
    attack_ms: float = 6.0
    release_ms: float = 70.0
    duck_db: float = 4.0
    needs_abs_travel: bool = True    # if the source only has a ride-height proxy, damp it


class GearshiftParams(EffectBase):
    level_up: float = Field(0.8, ge=0, le=1)
    level_down: float = Field(0.65, ge=0, le=1)
    attack_ms: float = 3.0
    hold_ms: float = 8.0
    decay_ms: float = 32.0
    duck_db: float = 5.0


class RevLimiterParams(EffectBase):
    freq_hz: float = 9.0
    duty: float = Field(0.55, gt=0, lt=1)
    level: float = Field(0.85, ge=0, le=1)
    duck_db: float = 4.0


class ImpactParams(EffectBase):
    trigger_g: float = 3.5
    ref_g: float = 12.0             # |accel| excess that maps to full scale
    damage_trigger: float = 0.02   # per-part damage delta
    attack_ms: float = 2.0
    hold_ms: float = 25.0
    decay_ms: float = 90.0
    cooldown_ms: float = 180.0
    duck_db: float = 8.0
    level: float = Field(1.0, ge=0, le=1)


class OfftrackParams(EffectBase):
    speed_min_ms: float = 4.0
    speed_ref_ms: float = 25.0
    noise_lo_hz: float = 8.0
    noise_hi_hz: float = 22.0
    level: float = Field(0.55, ge=0, le=1)
    use_tyre_dirt: bool = True      # UDP fallback when OFF_TRACK_COUNT is absent
    dirt_thr: float = 0.15


# --------------------------------------------------------------------------- #
#  mixer + engine
# --------------------------------------------------------------------------- #
class MixerParams(_Base):
    master_gain: float = Field(0.9, ge=0.0, le=2.0)
    bed_ceiling: float = Field(0.6, ge=0.0, le=1.0)
    limiter_knee: float = Field(0.7, gt=0.0, lt=1.0)
    duck_release_ms: float = 140.0
    gate_open: float = Field(0.02, ge=0.0, le=0.5)
    gate_close: float = Field(0.012, ge=0.0, le=0.5)
    gate_hold_ms: float = 120.0
    thermal_tau_s: float = Field(45.0, gt=1.0)
    thermal_soft: float = Field(0.18, gt=0.0, le=1.0)   # mean-square level at which foldback starts
    thermal_hard: float = Field(0.34, gt=0.0, le=1.0)
    output_curve: float = Field(1.0, gt=0.0)            # final gamma on 0..1


class EffectsConfig(_Base):
    road: RoadParams = Field(default_factory=RoadParams)
    engine: EngineRpmParams = Field(default_factory=EngineRpmParams)
    grip_scrub: GripScrubParams = Field(default_factory=GripScrubParams)
    lockup: LockupParams = Field(default_factory=LockupParams)
    wheelspin: WheelspinParams = Field(default_factory=WheelspinParams)
    abs_pulse: AbsPulseParams = Field(default_factory=AbsPulseParams)
    tc_cut: TcCutParams = Field(default_factory=TcCutParams)
    kerb: KerbParams = Field(default_factory=KerbParams)
    gearshift: GearshiftParams = Field(default_factory=GearshiftParams)
    rev_limiter: RevLimiterParams = Field(default_factory=RevLimiterParams)
    impact: ImpactParams = Field(default_factory=ImpactParams)
    offtrack: OfftrackParams = Field(default_factory=OfftrackParams)


class EngineConfig(_Base):
    effects: EffectsConfig = Field(default_factory=EffectsConfig)
    mixer: MixerParams = Field(default_factory=MixerParams)
    stale_ms: float = Field(250.0, gt=0)       # no fresh frame this long -> ramp to 0
    enable_ramp_ms: float = Field(180.0, gt=0)
    force_enable: bool = False                 # bench mode: run even off-track / stale
