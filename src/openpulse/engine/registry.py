"""Effect registry — the list of effects the pipeline builds, in display order."""

from __future__ import annotations

from openpulse.engine.effect import Effect
from openpulse.engine.effects.abs_pulse import AbsPulseEffect
from openpulse.engine.effects.engine_rpm import EngineRpmEffect
from openpulse.engine.effects.gearshift import GearshiftEffect
from openpulse.engine.effects.grip_scrub import GripScrubEffect
from openpulse.engine.effects.impact import ImpactEffect
from openpulse.engine.effects.kerb import KerbEffect
from openpulse.engine.effects.lockup import LockupEffect
from openpulse.engine.effects.offtrack import OfftrackEffect
from openpulse.engine.effects.rev_limiter import RevLimiterEffect
from openpulse.engine.effects.road import RoadEffect
from openpulse.engine.effects.tc_cut import TcCutEffect
from openpulse.engine.effects.wheelspin import WheelspinEffect
from openpulse.engine.params import EffectsConfig

# beds first, then events — this is also the GUI order
EFFECT_CLASSES: list[type[Effect]] = [
    RoadEffect,
    EngineRpmEffect,
    GripScrubEffect,
    LockupEffect,
    WheelspinEffect,
    AbsPulseEffect,
    TcCutEffect,
    KerbEffect,
    GearshiftEffect,
    RevLimiterEffect,
    ImpactEffect,
    OfftrackEffect,
]

EFFECT_IDS: list[str] = [c.id for c in EFFECT_CLASSES]


def build_effects(cfg: EffectsConfig) -> list[Effect]:
    return [cls(getattr(cfg, cls.id)) for cls in EFFECT_CLASSES]
