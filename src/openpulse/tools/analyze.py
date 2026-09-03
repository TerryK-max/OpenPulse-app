"""Percentile report over a recorded telemetry file.

Loads a capture, re-runs the Deriver (so DeriveConfig edits take effect), and
prints the distribution of the signals that drive Deriver + effect thresholds.
Use it to calibrate ``grip_scrub`` / impact / lockup / kerb thresholds against
a real lap.

    openpulse-analyze recordings/lap.jsonl
    openpulse-analyze recordings/lap.jsonl --on-track-only
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.record import load_frames


def _pcts(xs: list[float]) -> str:
    xs = sorted(v for v in xs if isinstance(v, (int, float)) and math.isfinite(v))
    if not xs:
        return "   (no finite samples)"

    def p(q: float) -> float:
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    return (f"n={len(xs):6d}  p50={p(.5):9.3f}  p75={p(.75):9.3f}  p90={p(.90):9.3f}  "
            f"p95={p(.95):9.3f}  p99={p(.99):9.3f}  max={xs[-1]:9.3f}")


def _finite(xs):
    return [x for x in xs if math.isfinite(x)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--on-track-only", action="store_true", help="ignore frames where on_track is false")
    a = ap.parse_args(argv)

    try:
        raw_frames = load_frames(a.path)
    except OSError as e:
        print(f"!! {e}", file=sys.stderr)
        return 1
    if not raw_frames:
        print("!! no frames", file=sys.stderr)
        return 1

    dv = Deriver()
    frames = [dv.process(f) for f in raw_frames]
    if a.on_track_only:
        frames = [f for f in frames if f.on_track] or frames

    series: dict[str, list[float]] = {
        "speed_ms": [f.speed_ms for f in frames],
        "|accel_g|": [math.sqrt(sum(c * c for c in f.accel_g)) for f in frames],
        "accel_lat": [f.accel_g[0] for f in frames],
        "accel_lon": [f.accel_g[1] for f in frames],
        "accel_vert": [f.accel_g[2] for f in frames],
        "jerk_g_s": [f.jerk_g_s for f in frames],
        "impact_g": [f.impact_g for f in frames],
        "combined_slip.max": [max(_finite(f.combined_slip), default=math.nan) for f in frames],
        "nd_slip.max": [max(_finite(f.nd_slip), default=math.nan) for f in frames],
        "grip_scrub": [f.grip_scrub for f in frames],
        "grip_scrub.front": [f.grip_scrub_front for f in frames],
        "grip_scrub.rear": [f.grip_scrub_rear for f in frames],
        "lock_ratio.front_min": [min(_finite(f.lock_ratio[:2]), default=math.nan) for f in frames],
        "lock_ratio.rear_max": [max(_finite(f.lock_ratio[2:]), default=math.nan) for f in frames],
        "|susp_vel|.max": [max((abs(v) for v in _finite(f.susp_vel_ms)), default=math.nan) for f in frames],
        "susp_travel.max": [max(_finite(f.susp_travel_m), default=math.nan) for f in frames],
        "brake": [f.brake for f in frames],
        "throttle": [f.throttle for f in frames],
        "dt_ms": [f.dt * 1000 for f in frames],
    }

    src = frames[0].source
    dur = frames[-1].t_mono - frames[0].t_mono
    on = sum(1 for f in frames if f.on_track)
    print(f"\n{a.path}")
    print(f"  source={src}  frames={len(frames)}  ~{dur:.1f}s  on_track={on}/{len(frames)}  "
          f"caps={frames[0].caps!r}")
    impact_edges = sum(
        1 for a, b in zip(frames, frames[1:], strict=False)
        if b.impact_g > 0.05 and a.impact_g <= 0.05
    )
    print(f"  gear changes: {sum(1 for f in frames if f.gear_change)}   "
          f"impact events: {impact_edges}   "
          f"rev-limiter frames: {sum(1 for f in frames if f.rev_limiter)}")
    if series["dt_ms"]:
        print(f"  frame dt: mean {statistics.fmean(series['dt_ms']):.2f} ms")
    print()
    for name, xs in series.items():
        print(f"  {name:22s} {_pcts(xs)}")

    _suggest(series)
    return 0


def _suggest(series: dict[str, list[float]]) -> None:
    def p(name: str, q: float) -> float:
        xs = sorted(v for v in series[name] if math.isfinite(v))
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else math.nan

    print("\nsuggested DeriveConfig (calibrate to taste):")
    cs90, cs99 = p("combined_slip.max", 0.90), p("combined_slip.max", 0.99)
    if math.isfinite(cs90):
        print(f"  scrub_wheelslip_lo ~= {cs90 * 0.6:.2f}   scrub_wheelslip_hi ~= {cs99:.2f}   "
              f"(combined_slip p90={cs90:.2f} p99={cs99:.2f})")
    am95, am99 = p("|accel_g|", 0.95), p("|accel_g|", 0.999)
    if math.isfinite(am95):
        print(f"  impact_excess_g ~= {max(2.5, (am99 - am95)):.1f}   "
              f"(|accel| p95={am95:.2f} p99.9={am99:.2f})")
    nd95 = p("nd_slip.max", 0.95)
    if math.isfinite(nd95):
        print(f"  scrub_nd_lo ~= {nd95 * 0.9:.2f}   scrub_nd_hi ~= {max(nd95 * 1.1, nd95 + 0.15):.2f}")


if __name__ == "__main__":
    raise SystemExit(main())
