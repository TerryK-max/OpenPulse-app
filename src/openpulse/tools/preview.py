"""Render a recorded telemetry file through the effect engine, offline.

Writes a WAV of the amplitude envelope modulating the box's ~158.7 Hz carrier
(so you can *hear* roughly what the box would feel), and prints a per-effect
report. The A/B tool for tuning effect params against a real lap.

    openpulse-preview recordings/lap.jsonl
    openpulse-preview recordings/lap.jsonl --out lap.wav --fs 8000
"""

from __future__ import annotations

import argparse
import math
import sys
import wave

import numpy as np

from openpulse.config import AppSettings
from openpulse.engine.pipeline import Pipeline
from openpulse.telemetry.derive import Deriver
from openpulse.telemetry.record import load_frames

CARRIER_HZ = 158.7


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--out", default=None, help="WAV path (default: <path>.wav)")
    ap.add_argument("--fs", type=int, default=8000, help="engine + WAV sample rate")
    ap.add_argument("--config", default=None, help="AppSettings JSON to load engine params from")
    a = ap.parse_args(argv)

    try:
        frames = load_frames(a.path)
    except OSError as e:
        print(f"!! {e}", file=sys.stderr)
        return 1
    if len(frames) < 10:
        print("!! recording too short", file=sys.stderr)
        return 1

    settings = AppSettings()
    if a.config:
        settings = AppSettings.model_validate_json(open(a.config, encoding="utf-8").read())

    pipe = Pipeline(settings.engine)
    dv = Deriver()
    fs = a.fs
    block = max(8, fs // 200)  # ~5 ms

    env_parts: list[np.ndarray] = []
    eff_peak: dict[str, float] = {}
    eff_active: dict[str, int] = {}
    n_blocks = 0
    gate_open_blocks = 0
    thermal_cut_blocks = 0

    t_prev = frames[0].t_mono
    for raw in frames:
        f = dv.process(raw)
        span = raw.t_mono - t_prev
        t_prev = raw.t_mono
        n_samp = int(round(min(max(span, 0.0), 0.1) * fs))
        if n_samp <= 0:
            n_samp = block
        pipe.set_frame(f)
        while n_samp > 0:
            take = min(block, n_samp)
            env_parts.append(pipe.generate(take, fs))
            n_samp -= take
            m = pipe.meters()
            n_blocks += 1
            if m.gate_gain > 0.5:
                gate_open_blocks += 1
            if m.thermal_gain < 0.98:
                thermal_cut_blocks += 1
            for k, v in m.effect_levels.items():
                eff_peak[k] = max(eff_peak.get(k, 0.0), v)
                if v > 0.05:
                    eff_active[k] = eff_active.get(k, 0) + 1

    env = np.concatenate(env_parts) if env_parts else np.zeros(fs, np.float32)
    t = np.arange(env.size) / fs
    carrier = np.sin(2 * math.pi * CARRIER_HZ * t)
    wav = np.clip(env * carrier, -1.0, 1.0)
    pcm = (wav * 32767).astype("<i2")

    out = a.out or (a.path + ".wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())

    dur = env.size / fs
    rms = float(np.sqrt(np.mean(env * env)))
    print(f"\n{a.path}  ->  {out}")
    print(f"  {dur:.1f}s @ {fs} Hz   env RMS {rms:.3f}   peak {float(np.max(env)):.3f}   "
          f"MS {rms * rms:.3f} (thermal_soft {settings.engine.mixer.thermal_soft})")
    print(f"  gate open {100 * gate_open_blocks / max(1, n_blocks):.0f}% of blocks   "
          f"thermal foldback {100 * thermal_cut_blocks / max(1, n_blocks):.0f}%")
    print("\n  effect            peak   active%")
    for k in eff_peak:
        act = 100 * eff_active.get(k, 0) / max(1, n_blocks)
        print(f"  {k:16s} {eff_peak[k]:5.2f}   {act:5.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
