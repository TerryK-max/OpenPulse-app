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
    testing: str = ""            # effect id currently under the GUI "Test" button
    test_remaining_s: float = 0.0


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
        # GUI "Test" button: [effect, start sample index (None until the first
        # block), duration in seconds]. Solo-plays that one effect for a moment.
        self._test: list | None = None
        # (t_seconds, sample) — one point per block (decimated), for the GUI scope.
        # maxlen is generous (covers ~10 s even at a fast ~2 kHz block rate); the
        # GUI windows this down to a few seconds via history(seconds=...).
        self._history: deque[tuple[float, float]] = deque(maxlen=20000)

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
            test = self._test
        fresh = frame is not None and (time.perf_counter() - frame_at) * 1000.0 <= self.cfg.stale_ms
        use = frame if (frame is not None) else _IDLE_FRAME
        live = fresh and use.on_track and not use.paused
        if self.cfg.force_enable and not live:
            # bench mode: run the effects against whatever telemetry we have
            from dataclasses import replace as _replace
            use = _replace(use, on_track=True, paused=False)
        t0 = 0.0
        if test is not None:
            if test[1] is None:           # first block of this test
                test[1] = self._sample_idx
                test[0].reset()           # on this thread, not the GUI's
            t0 = (self._sample_idx - test[1]) / fs
            if t0 >= test[2]:
                with self._lock:
                    if self._test is test:
                        self._test = None
                test = None
        want_enable = 1.0 if (live or self.cfg.force_enable) else 0.0

        dt = n / fs
        if test is not None:
            self._enable = 1.0            # no fade-in: it would eat a short thump
        else:
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
            if test is not None:
                # solo: only the tested effect sounds, whatever its "on" box says
                if e is test[0]:
                    tctx = EffectContext(
                        fs=fs, n=n, dt=dt, t=ctx.t, rng=self.rng,
                        frame=e.test_frame(use, t0, t0 + dt),
                    )
                    b = e.render(tctx)
                else:
                    b = e.silent(n)
            else:
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
            testing=test[0].id if test is not None else "",
            test_remaining_s=max(0.0, test[2] - t0) if test is not None else 0.0,
        )
        if sig.size:
            self._history.append((self._sample_idx / fs, float(sig[-1])))
        return sig

    # -- outputs ------------------------------------------------------ #
    def meters(self) -> PipelineMeters:
        return self._meters

    def history(self, seconds: float | None = None) -> list[tuple[float, float]]:
        """(t_seconds, sample) points, oldest first. ``seconds`` windows it down
        to the trailing N seconds (None = the whole buffer)."""
        hist = self._history
        if not hist or seconds is None:
            return list(hist)
        t_start = hist[-1][0] - seconds
        out: list[tuple[float, float]] = []
        for point in reversed(hist):
            if point[0] < t_start:
                break
            out.append(point)
        out.reverse()
        return out

    def trigger_test(self, effect_id: str) -> bool:
        """GUI 'Test' button: solo-play one effect for its ``test_duration_s``
        against a synthetic frame that makes it fire — independent of its "on"
        box, the live telemetry and the on-track gate. False if no such effect."""
        for e in self.effects:
            if e.id == effect_id:
                with self._lock:
                    self._test = [e, None, e.test_duration_s]
                return True
        return False
