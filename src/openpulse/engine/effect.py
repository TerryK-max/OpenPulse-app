"""Effect base class + the per-block context."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np

from openpulse.telemetry.frame import TelemetryFrame


@dataclass(slots=True)
class EffectContext:
    fs: int
    n: int
    dt: float                     # block duration, n / fs
    t: float                      # seconds since the pipeline started
    frame: TelemetryFrame
    rng: np.random.Generator


@dataclass(slots=True)
class EffectBlock:
    signal: np.ndarray            # float32 [n], nominally 0..1 (mixer clamps)
    duck: float = 0.0             # dB of bed ducking requested while prominent
    level: float = 0.0            # 0..1 scalar for the GUI meter
    meta: dict = field(default_factory=dict)


class Effect(abc.ABC):
    #: stable id, matches the params field name
    id: str = "effect"
    #: "bed" (continuous) or "event" (triggered / gated)
    kind: str = "bed"
    #: if True the mixer attenuates this effect when something requests a duck
    #: (the ambient road/engine bed); grip_scrub and events set this False
    duckable: bool = False

    def __init__(self, params) -> None:  # noqa: ANN001 - concrete pydantic model per effect
        self.p = params
        self.reset()

    @property
    def enabled(self) -> bool:
        return bool(self.p.enabled)

    def reset(self) -> None:  # noqa: B027 - optional override
        """Clear internal DSP state. Called on (re)start."""

    @abc.abstractmethod
    def render(self, ctx: EffectContext) -> EffectBlock: ...

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def silent(n: int) -> EffectBlock:
        return EffectBlock(signal=np.zeros(n, np.float32))
