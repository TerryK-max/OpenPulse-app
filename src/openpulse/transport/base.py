"""Transport abstraction: a bidirectional pipe that carries whole protocol frames.

Per PROTOCOL.md §1 every delivery unit is exactly one frame (USB bulk transfer,
later one RF packet), so the transport deals in frames, not a byte stream. The
protocol/framing logic lives in ``protocol.py``; the handshake and reader thread
live in ``openpulse.box.session``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


class TransportError(Exception):
    """Base class for transport failures."""


class DeviceNotFoundError(TransportError):
    """No matching device is connected."""


class InterfaceClaimError(TransportError):
    """The device is present but its vendor interface could not be claimed.

    On Windows this almost always means the vendor interface still has the
    default driver bound and needs a one-time WinUSB/libusbK bind via Zadig
    (the firmware has no MS OS 2.0 descriptors yet — firmware ROADMAP 3.6).
    On Linux it usually means a missing udev rule or insufficient permissions.
    """


@dataclass(slots=True)
class TransportStats:
    frames_sent: int = 0
    frames_recv: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    send_errors: int = 0
    recv_errors: int = 0
    extra: dict[str, int] = field(default_factory=dict)


class Transport(abc.ABC):
    """A frame pipe to the box."""

    #: human-readable kind, e.g. "usb" / "mock"
    kind: str = "abstract"

    @abc.abstractmethod
    def open(self) -> None:
        """Acquire the pipe. Raises a TransportError subclass on failure."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release the pipe. Idempotent, never raises."""

    @abc.abstractmethod
    def send_frame(self, frame: bytes) -> None:
        """Send one already-assembled frame. Raises TransportError on failure."""

    @abc.abstractmethod
    def read_frame(self, timeout: float) -> bytes | None:
        """Return one raw inbound frame, or None if none arrived within ``timeout`` s."""

    @property
    @abc.abstractmethod
    def stats(self) -> TransportStats: ...

    @property
    def is_open(self) -> bool:
        return getattr(self, "_open", False)

    def __enter__(self) -> Transport:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
