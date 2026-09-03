"""TelemetrySource abstraction + session metadata."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import StrEnum

from openpulse.telemetry.frame import Cap, TelemetryFrame


class TelemetryError(Exception):
    """Base class for telemetry acquisition failures."""


class TelemetryUnavailable(TelemetryError):
    """The source cannot run here (game not found, wrong OS, port busy...)."""


class SourceStatus(StrEnum):
    OFFLINE = "offline"      # cannot reach the game
    CONNECTED = "connected"  # game running, but not driving (menu / replay / pause / pits)
    LIVE = "live"            # physics running, driver in control


@dataclass(slots=True)
class SessionInfo:
    game: str = ""
    car: str = ""
    track: str = ""
    track_config: str = ""
    driver: str = ""
    rpm_max: float = 0.0

    def label(self) -> str:
        t = self.track + (f" [{self.track_config}]" if self.track_config else "")
        return " · ".join(p for p in (self.car, t) if p) or "(no session)"


class TelemetrySource(abc.ABC):
    """A stream of :class:`TelemetryFrame`. One concrete class per game + method."""

    name: str = "abstract"

    @abc.abstractmethod
    def open(self) -> None:
        """Start acquisition. Raises TelemetryUnavailable if it cannot."""

    @abc.abstractmethod
    def close(self) -> None:
        """Stop acquisition. Idempotent, never raises."""

    @abc.abstractmethod
    def status(self) -> SourceStatus: ...

    @abc.abstractmethod
    def capabilities(self) -> Cap: ...

    @abc.abstractmethod
    def session_info(self) -> SessionInfo | None: ...

    @abc.abstractmethod
    def poll(self, timeout: float) -> TelemetryFrame | None:
        """Return the latest frame. May block up to ``timeout`` s waiting for
        fresh data; returns None if nothing is available in that time."""

    @property
    def is_open(self) -> bool:
        return getattr(self, "_open", False)

    def __enter__(self) -> TelemetrySource:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
