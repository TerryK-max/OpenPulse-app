"""The effect engine — telemetry -> a single amplitude envelope for the box.

Every effect outputs an amplitude-modulation signal in 0..1 (the box drives one
LRA at a fixed ~158.7 Hz carrier, so we shape its *amplitude*, not a waveform).
Useful modulation band is ~1-40 Hz; above that the actuator can't keep up.
"""

from openpulse.engine.effect import Effect, EffectBlock, EffectContext
from openpulse.engine.params import EngineConfig, MixerParams
from openpulse.engine.pipeline import Pipeline
from openpulse.engine.registry import EFFECT_IDS, build_effects

__all__ = [
    "Effect",
    "EffectBlock",
    "EffectContext",
    "EngineConfig",
    "MixerParams",
    "Pipeline",
    "build_effects",
    "EFFECT_IDS",
]
