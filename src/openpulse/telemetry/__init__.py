"""Telemetry acquisition + normalization.

Game-specific sources produce a game-agnostic :class:`TelemetryFrame`; the
:class:`Deriver` fills cross-frame quantities (suspension velocity, lock ratio,
jerk, impacts, grip scrub). Effects (Phase C) only ever see the normalized,
enriched frame.
"""

from openpulse.telemetry.base import (
    SessionInfo,
    SourceStatus,
    TelemetryError,
    TelemetrySource,
    TelemetryUnavailable,
)
from openpulse.telemetry.derive import DeriveConfig, Deriver
from openpulse.telemetry.frame import WHEELS, Cap, TelemetryFrame
from openpulse.telemetry.registry import build_source

__all__ = [
    "TelemetryFrame",
    "Cap",
    "WHEELS",
    "TelemetrySource",
    "SourceStatus",
    "SessionInfo",
    "TelemetryError",
    "TelemetryUnavailable",
    "Deriver",
    "DeriveConfig",
    "build_source",
]
