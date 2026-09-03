"""Pipeline — latest telemetry frame -> effects -> mixer -> one 0..1 block.

The renderer/streamer pulls blocks from :meth:`generate`. Telemetry is
zero-order-held between updates; effects run per-sample so texture is preserved.
When telemetry goes stale or the car leaves the track, the output ramps to 0.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from openpulse.engine.effect import EffectContext
from openpulse.engine.mixer import Mixer, MixOutput
from openpulse.engine.params import EngineConfig
from openpulse.engine.registry import build_effects
from openpulse.telemetry.frame import TelemetryFrame

_IDLE_FRAME = TelemetryFrame(t_mono=0.0, source="idle")


@dataclass(slots=True)
class PipelineMeters:
    output_peak: float = 0.0
    output_rms: float = 0.0
    enable: float = 0.0
    duck_gain: float = 1.0
    gate_gain: float = 1.0
    thermal_gain: float = 1.0
    thermal_energy: float = 0.0
    effect_levels: dict[str, float] = field(default_factory=dict)
    telemetry_fresh: bool = False
    blocks: int = 0


class Pipeline:
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.effects = build_effects(cfg.effects)
        self.mixer = Mixer(cfg.mixer)
        self.rng = np.random.default_rng(0x0DDBA11)
        self._lock = threading.Lock()
        self._frame: TelemetryFrame | None = None
        self._frame_at = 0.0
        self._sample_idx = 0
        self._enable = 0.0
        self._external_limit = 1.0
        self._meters = PipelineMeters()
        self._history: deque[float] = deque(maxlen=4096)   # decimated output for the GUI scope

    # -- inputs ---------------------------------------------------------- #
    def set_frame(self, frame: TelemetryFrame | None) -> None:
        with self._lock:
            self._frame = frame
            self._frame_at = time.perf_counter()

    def set_external_limit(self, limit: float) -> None:
        """0..1 — clamp from the box thermal / fault state (1 = no limit)."""
        self._external_limit = max(0.0, min(1.0, limit))

    def reset(self) -> None:
        for e in self.effects:
            e.reset()
        self.mixer.reset()
        self._sample_idx = 0
        self._enable = 0.0
        self._history.clear()

    # -- generate ------------------------------------------------------ #
    def generate(self, n: int, fs: int) -> np.ndarray:
        with self._lock:
            frame = self._frame
            frame_at = self._frame_at
        fresh = frame is not None and (time.perf_counter() - frame_at) * 1000.0 <= self.cfg.stale_ms
        use = frame if (frame is not None) else _IDLE_FRAME
        live = fresh and use.on_track and not use.paused
        if self.cfg.force_enable and not live:
            # bench mode: run the effects against whatever telemetry we have
            from dataclasses import replace as _replace
            use = _replace(use, on_track=True, paused=False)
        want_enable = 1.0 if (live or self.cfg.force_enable) else 0.0

        dt = n / fs
        ramp = dt / max(1e-3, self.cfg.enable_ramp_ms / 1000.0)
        self._enable += max(-ramp, min(ramp, want_enable - self._enable))

        ctx = EffectContext(
            fs=fs, n=n, dt=dt, t=self._sample_idx / fs, frame=use, rng=self.rng
        )
        self.mixer.sync_params()
        blocks = {}
        kinds = {}
        duckable = {}
        for e in self.effects:
            kinds[e.id] = e.kind
            duckable[e.id] = e.duckable
            b = e.render(ctx) if e.enabled else e.silent(n)
            if b.signal.size:
                b.signal = np.clip(b.signal, 0.0, 1.0).astype(np.float32, copy=False)
                b.level = float(np.max(b.signal))
            blocks[e.id] = b

        out: MixOutput = self.mixer.mix(
            blocks, dt, external_limit=self._external_limit, enable=self._enable,
            kinds=kinds, duckable=duckable,
        )
        self._sample_idx += n

        sig = out.signal
        self._meters = PipelineMeters(
            output_peak=out.peak,
            output_rms=float(np.sqrt(np.mean(sig * sig))) if sig.size else 0.0,
            enable=self._enable,
            duck_gain=out.duck_gain,
            gate_gain=out.gate_gain,
            thermal_gain=out.thermal_gain,
            thermal_energy=out.thermal_energy,
            effect_levels={k: (v if v == v else 0.0) for k, v in out.effect_levels.items()},
            telemetry_fresh=fresh,
            blocks=self._meters.blocks + 1,
        )
        if sig.size:
            self._history.append(float(sig[-1]))
        return sig

    # -- outputs ------------------------------------------------------ #
    def meters(self) -> PipelineMeters:
        return self._meters

    def history(self) -> list[float]:
        return list(self._history)

    def trigger_test(self, effect_id: str) -> None:
        """GUI 'Test' button — nudge an effect's telemetry-independent trigger."""
        for e in self.effects:
            if e.id == effect_id and hasattr(e, "_env"):
                env = e._env
                if hasattr(env, "trigger"):
                    env.trigger(3.0, 20.0, 90.0, 0.9)
