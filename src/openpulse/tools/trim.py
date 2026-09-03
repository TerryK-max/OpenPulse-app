"""Trim a big capture into a small committable fixture.

    openpulse-trim recordings/ac_shm_full.jsonl tests/fixtures/ac_shm_spa.jsonl --every 20

Keeps every Nth frame (and forces a keep around gear changes / impacts so the
interesting bits survive), rewrites compact (no NaN / derived fields), and copies
the .meta.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.record import frame_to_json, load_frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--every", type=int, default=20, help="keep 1 frame in N")
    ap.add_argument("--max", type=int, default=1500, help="hard cap on kept frames")
    a = ap.parse_args(argv)

    frames = load_frames(a.src)
    if not frames:
        print("!! empty source")
        return 1

    dv = Deriver()
    keep_idx: set[int] = set()
    prev_gear = frames[0].gear
    for i, raw in enumerate(frames):
        f = dv.process(raw)
        if i % a.every == 0:
            keep_idx.add(i)
        if f.gear != prev_gear or f.impact_g > 0.05:
            for j in range(max(0, i - 2), min(len(frames), i + 3)):
                keep_idx.add(j)
        prev_gear = f.gear

    kept = sorted(keep_idx)
    if len(kept) > a.max:
        step = len(kept) / a.max
        kept = [kept[int(k * step)] for k in range(a.max)]

    dst = Path(a.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as fh:
        for i in kept:
            fh.write(json.dumps(frame_to_json(frames[i], compact=True)) + "\n")

    meta_src = Path(a.src + ".meta.json")
    if meta_src.exists():
        shutil.copyfile(meta_src, str(dst) + ".meta.json")

    src_mb = Path(a.src).stat().st_size / 1e6
    dst_mb = dst.stat().st_size / 1e6
    print(f"{len(frames)} -> {len(kept)} frames   {src_mb:.1f} MB -> {dst_mb:.2f} MB   {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
