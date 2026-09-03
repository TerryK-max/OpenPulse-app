"""Capture Assetto Corsa telemetry to a fixture, with a sanity report.

    python -m openpulse.tools.telemetry_capture --source ac-shm --seconds 30 --out FIX/ac_shm_lap.jsonl
    python -m openpulse.tools.telemetry_capture --source ac-udp --host 192.168.1.20 --out FIX/ac_udp_lap.jsonl
    python -m openpulse.tools.telemetry_capture --source synthetic --scenario grip_scrub --out FIX/synth.jsonl

(FIX = tests/fixtures/)

``--raw`` (ac-shm / ac-udp) also dumps the raw struct bytes to ``<out>.raw.bin``
for byte-offset verification, and prints per-field min/max/mean so you can catch
a wrong unit or axis before committing the fixture.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.frame import WHEELS, Cap, TelemetryFrame
from openpulse.telemetry.record import TelemetryRecorder, frame_to_json
from openpulse.telemetry.registry import build_source


def _sanity(frames: list[TelemetryFrame]) -> None:
    if not frames:
        print("  no frames captured")
        return
    keys = ["speed_ms", "rpm", "gear", "throttle", "brake", "steer", "grip_scrub", "jerk_g_s"]
    print("\nscalar ranges:")
    for k in keys:
        vals = [getattr(f, k) for f in frames if math.isfinite(getattr(f, k))]
        if vals:
            print(f"  {k:14s} min={min(vals):9.3f}  max={max(vals):9.3f}  mean={statistics.fmean(vals):9.3f}")
    print("\nper-wheel ranges (FL FR RL RR), last frame:")
    lf = frames[-1]
    for attr in ("wheel_omega", "lock_ratio", "combined_slip", "nd_slip", "load_n", "susp_travel_m", "tyre_temp_c"):
        v = getattr(lf, attr)
        print(f"  {attr:14s} " + " ".join(f"{x:8.3f}" for x in v))
    caps = frames[-1].caps
    have = [c.name for c in Cap if c != Cap.NONE and c in caps]
    print("\ncapabilities:", ", ".join(have) or "(none)")
    print("wheel order assumed:", WHEELS)
    ont = sum(1 for f in frames if f.on_track)
    print(f"on_track frames: {ont}/{len(frames)}   packet_id span: "
          f"{frames[0].packet_id}..{frames[-1].packet_id}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, choices=("ac-shm", "ac-udp", "synthetic", "replay"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9996)
    ap.add_argument("--scenario", default="hotlap")
    ap.add_argument("--replay-path", default="")
    ap.add_argument("--rpm-max", type=float, default=0.0, help="per-car rev limit hint for ac-udp")
    ap.add_argument("--raw", action="store_true", help="also dump raw struct bytes + field stats")
    ap.add_argument("--no-derive", action="store_true", help="store raw frames, skip the Deriver")
    a = ap.parse_args(argv)

    try:
        src = build_source(
            a.source, udp_host=a.host, udp_port=a.port,
            replay_path=a.replay_path, scenario=a.scenario, rpm_max_hint=a.rpm_max,
        )
        src.open()
    except Exception as e:  # noqa: BLE001
        print(f"!! cannot open source: {e}", file=sys.stderr)
        return 1

    deriver = Deriver()
    raw_fh = open(a.out + ".raw.bin", "wb") if a.raw and a.source in ("ac-shm", "ac-udp") else None
    frames: list[TelemetryFrame] = []
    rec = TelemetryRecorder(a.out, src.session_info(), src.name)
    print(f"capturing {a.source} for {a.seconds:.0f}s -> {a.out}  (Ctrl-C to stop early)")
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < a.seconds:
            f = src.poll(0.05)
            if f is None:
                continue
            if raw_fh is not None:
                raw_fh.write(_raw_bytes(src))
            frame = f if a.no_derive else deriver.process(f)
            rec.write(frame)
            frames.append(frame)
            if len(frames) % 200 == 0:
                print(f"  {len(frames)} frames  ({src.status().value})")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        rec.close()
        if raw_fh is not None:
            raw_fh.close()
        src.close()

    print(f"\nwrote {rec.count} frames to {a.out}")
    _sanity(frames)
    if frames:
        print("\nfirst frame json:\n ", frame_to_json(frames[0]))
    return 0


def _raw_bytes(src) -> bytes:  # noqa: ANN001
    # best-effort raw dump for offset verification
    import ctypes

    try:
        if src.name == "ac/shm":
            from openpulse.telemetry.ac import structs as S

            return b"".join(
                bytes(src._mm[k][: ctypes.sizeof(t)])  # type: ignore[index]
                for k, t in (("phys", S.ACPhysics), ("graph", S.ACGraphics), ("stat", S.ACStatic))
            )
    except Exception:
        pass
    return b""


if __name__ == "__main__":
    raise SystemExit(main())
