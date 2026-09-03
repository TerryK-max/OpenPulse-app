"""Cross-frame derivations: everything the effect engine needs that a single
raw frame does not carry.

Held state (previous frame, one-pole filters, peak/hold detectors) lives in the
:class:`Deriver`; :meth:`Deriver.process` takes a normalized frame and returns an
enriched copy. Thresholds are in :class:`DeriveConfig` and are tunable from the
GUI later — the defaults are first guesses to be calibrated against a real
capture.
"""

from __future__ import annotations

import math
from dataclasses import replace

from pydantic import BaseModel, ConfigDict

from openpulse.telemetry.frame import Cap, TelemetryFrame, Wheel4

_NAN = math.nan


_DT_FLOOR = 1e-3  # s — below this a "frame" is a duplicate poll; don't differentiate


class DeriveConfig(BaseModel):
    default_tyre_radius_m: float = 0.33
    susp_vel_lp_hz: float = 30.0
    accel_lp_hz: float = 40.0
    # impact: keyed on |accel| rising above a slow baseline, NOT raw jerk
    # (a real AC capture on kerbs shows jerk routinely at 30-100 g/s and
    # spiking to thousands when a wheel drops off track).
    impact_baseline_hz: float = 1.5
    impact_excess_g: float = 4.0    # calibrated: |accel| p95≈1.7, p99≈2.2, crash 8-14 g
    impact_hold_s: float = 0.10
    impact_cooldown_s: float = 0.18
    impact_decay_s: float = 0.15
    limiter_frac: float = 0.992
    lock_ratio_min_speed_ms: float = 1.5
    # grip scrub: nd_slip near/above 1.0 = at the limit (UDP)
    scrub_nd_lo: float = 0.85
    scrub_nd_hi: float = 1.15
    # grip scrub fallback: AC WheelSlip (contact-patch slip speed). Calibrated
    # from a real lap: combined_slip.max p50≈0.74, p90≈1.8, p99≈8.4 (slide).
    # lo/hi span the "squealing near the limit, not yet sliding" band.
    scrub_wheelslip_lo: float = 0.8
    scrub_wheelslip_hi: float = 3.5
    # rough ABS / TC estimation when the source has no flag
    abs_est_brake_min: float = 0.35
    abs_est_lock_lo: float = 0.55
    abs_est_lock_hi: float = 0.97
    tc_est_throttle_min: float = 0.5
    tc_est_spin_ratio: float = 1.06

    model_config = ConfigDict(extra="forbid")


def _lp_alpha(fc_hz: float, dt: float) -> float:
    if dt <= 0 or fc_hz <= 0:
        return 1.0
    x = 2 * math.pi * fc_hz * dt
    return x / (x + 1.0)


def _smoothstep(lo: float, hi: float, x: float) -> float:
    if hi <= lo:
        return 0.0
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return t * t * (3 - 2 * t)


def _finite(xs) -> list[float]:
    return [x for x in xs if isinstance(x, float) and math.isfinite(x)]


class Deriver:
    def __init__(self, cfg: DeriveConfig | None = None):
        self.cfg = cfg or DeriveConfig()
        self.reset()

    def reset(self) -> None:
        self._prev: TelemetryFrame | None = None
        self._last_out: TelemetryFrame | None = None
        self._susp_vel: list[float] = [_NAN, _NAN, _NAN, _NAN]
        self._accel_mag_lp: float = _NAN
        self._accel_baseline: float = _NAN
        self._jerk_lp: float = 0.0
        self._impact: float = 0.0
        self._impact_until: float = 0.0
        self._impact_cool_until: float = 0.0
        self._rpm_max_seen: float = 0.0

    # ------------------------------------------------------------------ #
    def process(self, f: TelemetryFrame) -> TelemetryFrame:
        cfg = self.cfg
        prev = self._prev

        dt = f.dt
        if dt <= 0:
            dt = (f.t_mono - prev.t_mono) if prev is not None else 0.0
        dt = min(max(dt, 0.0), 0.1)

        # a duplicate poll (same game packet, or dt below the differentiation
        # floor): re-serve the last enriched frame with a fresh timestamp so
        # derivative terms never see a spurious tiny dt.
        if (
            prev is not None
            and self._last_out is not None
            and (
                (f.packet_id != 0 and f.packet_id == prev.packet_id)
                or dt < _DT_FLOOR
            )
        ):
            self._last_out = replace(self._last_out, t_mono=f.t_mono, dt=dt)
            return self._last_out

        self._rpm_max_seen = max(self._rpm_max_seen, f.rpm)
        rpm_max = f.rpm_max if f.rpm_max > 0 else self._rpm_max_seen

        radius = self._radius(f)
        wheel_speed = self._wheel_speed(f, radius)
        lock_ratio = self._lock_ratio(f, wheel_speed)
        susp_vel = self._susp_velocity(f, prev, dt)
        jerk, impact = self._impacts(f, dt)
        gear_change = 0
        if prev is not None and f.gear != prev.gear:
            gear_change = 1 if f.gear > prev.gear else -1
        rev_limiter = f.engine_limiter or (rpm_max > 0 and f.rpm >= rpm_max * cfg.limiter_frac)

        abs_active, tc_active = self._abs_tc(f, lock_ratio)
        scrub, scrub_f, scrub_r = self._grip_scrub(f)

        self._prev = f
        out = replace(
            f,
            dt=dt,
            rpm_max=rpm_max,
            wheel_speed_ms=wheel_speed,
            tyre_radius_m=radius,
            lock_ratio=lock_ratio,
            susp_vel_ms=susp_vel,
            jerk_g_s=jerk,
            impact_g=impact,
            gear_change=gear_change,
            rev_limiter=rev_limiter,
            abs_active=abs_active,
            tc_active=tc_active,
            grip_scrub=scrub,
            grip_scrub_front=scrub_f,
            grip_scrub_rear=scrub_r,
        )
        self._last_out = out
        return out

    # ------------------------------------------------------------------ #
    def _radius(self, f: TelemetryFrame) -> Wheel4:
        if Cap.TYRE_RADIUS in f.caps and all(math.isfinite(r) and r > 0 for r in f.tyre_radius_m):
            return f.tyre_radius_m
        r = self.cfg.default_tyre_radius_m
        return (r, r, r, r)

    def _wheel_speed(self, f: TelemetryFrame, radius: Wheel4) -> Wheel4:
        if all(math.isfinite(v) for v in f.wheel_speed_ms):
            return f.wheel_speed_ms
        if Cap.WHEEL_OMEGA in f.caps:
            return tuple(  # type: ignore[return-value]
                (abs(o) * r if math.isfinite(o) else _NAN) for o, r in zip(f.wheel_omega, radius, strict=True)
            )
        return (_NAN, _NAN, _NAN, _NAN)

    def _lock_ratio(self, f: TelemetryFrame, wheel_speed: Wheel4) -> Wheel4:
        if f.speed_ms < self.cfg.lock_ratio_min_speed_ms:
            return (_NAN, _NAN, _NAN, _NAN)
        v = f.speed_ms
        return tuple((ws / v if math.isfinite(ws) else _NAN) for ws in wheel_speed)  # type: ignore[return-value]

    def _susp_velocity(self, f: TelemetryFrame, prev: TelemetryFrame | None, dt: float) -> Wheel4:
        if prev is None or dt < _DT_FLOOR:
            return tuple(self._susp_vel)  # type: ignore[return-value]
        a = _lp_alpha(self.cfg.susp_vel_lp_hz, dt)
        for i in range(4):
            cur, old = f.susp_travel_m[i], prev.susp_travel_m[i]
            if math.isfinite(cur) and math.isfinite(old):
                raw = (cur - old) / dt
                self._susp_vel[i] = raw if not math.isfinite(self._susp_vel[i]) else (
                    self._susp_vel[i] + a * (raw - self._susp_vel[i])
                )
        return tuple(self._susp_vel)  # type: ignore[return-value]

    def _impacts(self, f: TelemetryFrame, dt: float) -> tuple[float, float]:
        cfg = self.cfg
        dt_eff = max(dt, _DT_FLOOR)
        mag = math.sqrt(sum(c * c for c in f.accel_g))

        if not math.isfinite(self._accel_mag_lp):
            self._accel_mag_lp = mag
            self._accel_baseline = mag
        prev_lp = self._accel_mag_lp
        self._accel_mag_lp += _lp_alpha(cfg.accel_lp_hz, dt_eff) * (mag - self._accel_mag_lp)
        self._accel_baseline += _lp_alpha(cfg.impact_baseline_hz, dt_eff) * (mag - self._accel_baseline)
        jerk = abs(mag - prev_lp) / dt_eff
        self._jerk_lp += 0.4 * (jerk - self._jerk_lp)

        now = f.t_mono
        excess = mag - self._accel_baseline
        if excess > cfg.impact_excess_g and now >= self._impact_cool_until:
            self._impact = excess
            self._impact_until = now + cfg.impact_hold_s
            self._impact_cool_until = now + cfg.impact_cooldown_s
        elif now > self._impact_until:
            self._impact *= 0.5 ** (dt_eff / max(cfg.impact_decay_s, 1e-3))
            if self._impact < 0.05:
                self._impact = 0.0
        return self._jerk_lp, self._impact

    def _abs_tc(self, f: TelemetryFrame, lock_ratio: Wheel4) -> tuple[bool, bool]:
        cfg = self.cfg
        abs_active = f.abs_active
        tc_active = f.tc_active
        if Cap.ABS_FLAG not in f.caps and f.abs_available and f.brake > cfg.abs_est_brake_min:
            fronts = _finite(lock_ratio[:2])
            if fronts and min(fronts) < cfg.abs_est_lock_hi and max(fronts) > cfg.abs_est_lock_lo:
                abs_active = min(fronts) < cfg.abs_est_lock_hi
        if Cap.TC_FLAG not in f.caps and f.tc_available and f.throttle > cfg.tc_est_throttle_min:
            rears = _finite(lock_ratio[2:])
            if rears and max(rears) > cfg.tc_est_spin_ratio:
                tc_active = True
        return abs_active, tc_active

    def _grip_scrub(self, f: TelemetryFrame) -> tuple[float, float, float]:
        cfg = self.cfg
        if Cap.ND_SLIP in f.caps:
            vals = _finite(f.nd_slip)
            fr = _finite(f.nd_slip[:2])
            re = _finite(f.nd_slip[2:])
            lo, hi = cfg.scrub_nd_lo, cfg.scrub_nd_hi
        elif Cap.COMBINED_SLIP in f.caps:
            vals = _finite(f.combined_slip)
            fr = _finite(f.combined_slip[:2])
            re = _finite(f.combined_slip[2:])
            lo, hi = cfg.scrub_wheelslip_lo, cfg.scrub_wheelslip_hi
        else:
            return (_NAN, _NAN, _NAN)

        def scrub(xs: list[float]) -> float:
            return _smoothstep(lo, hi, max(xs)) if xs else _NAN

        return scrub(vals), scrub(fr), scrub(re)
