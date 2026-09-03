"""USB vendor-bulk transport (PROTOCOL.md §1, HARDWARE.md §5).

The box enumerates as a composite device: CDC-ACM (interfaces 0-1, a serial log
port owned by the OS) + a vendor interface (2, class 0xFF) carrying the framed
protocol on EP2 OUT/IN. We claim only interface 2 and leave the CDC pair alone.

This is a direct port of ``OpenPulse-firmware/tools/pc_sender/openpulse_send.py``
``open_box()`` — that path is bench-validated.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpulse.transport.base import (
    DeviceNotFoundError,
    InterfaceClaimError,
    Transport,
    TransportError,
    TransportStats,
)

USB_VID = 0x1A86  # Nanjing Qinheng (WCH)
USB_PID = 0x5730  # OpenPulse firmware (composite)
EP_OUT = 0x02
EP_IN = 0x82
VENDOR_IFACE = 2
_MAX_PACKET = 64  # FS bulk


@dataclass(slots=True)
class UsbDeviceInfo:
    vid: int
    pid: int
    product: str
    serial: str
    bus: int | None
    address: int | None

    def __str__(self) -> str:
        return f"{self.vid:#06x}:{self.pid:#06x}  {self.product or '?'}  (bus {self.bus} addr {self.address})"


def _usb():
    try:
        import usb.core
        import usb.util
    except ModuleNotFoundError as e:  # pragma: no cover - env dependent
        raise TransportError("pyusb is not installed (`pip install pyusb`).") from e
    return usb.core, usb.util


def _backend():
    """A libusb-1.0 backend. Prefers the bundled one from ``libusb-package`` so
    it works with no system install; falls back to whatever pyusb finds."""
    try:
        import libusb_package

        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


def _find(usb_core, **kwargs):
    try:
        return usb_core.find(backend=_backend(), **kwargs)
    except Exception as e:  # NoBackendError (ValueError) and friends
        raise TransportError(
            f"no usable libusb backend: {e}. Install a libusb-1.0 runtime "
            f"(Linux: libusb-1.0-0; macOS: `brew install libusb`) or the "
            f"`libusb-package` wheel."
        ) from e


def list_devices(vid: int = USB_VID, pid: int | None = None) -> list[UsbDeviceInfo]:
    """Enumerate matching USB devices (for a --list / rescan UI action)."""
    usb_core, usb_util = _usb()
    kwargs: dict[str, int] = {"idVendor": vid}
    if pid is not None:
        kwargs["idProduct"] = pid
    out: list[UsbDeviceInfo] = []
    for d in _find(usb_core, find_all=True, **kwargs):
        try:
            product = usb_util.get_string(d, d.iProduct) or ""
        except Exception:
            product = ""
        try:
            serial = usb_util.get_string(d, d.iSerialNumber) or ""
        except Exception:
            serial = ""
        out.append(
            UsbDeviceInfo(
                d.idVendor, d.idProduct, product, serial,
                getattr(d, "bus", None), getattr(d, "address", None),
            )
        )
    return out


class UsbVendorTransport(Transport):
    kind = "usb"

    def __init__(self, vid: int = USB_VID, pid: int = USB_PID):
        self.vid = vid
        self.pid = pid
        self._dev = None
        self._usb_util = None
        self._open = False
        self._stats = TransportStats()

    # -- lifecycle --------------------------------------------------------- #
    def open(self) -> None:
        if self._open:
            return
        usb_core, usb_util = _usb()
        self._usb_util = usb_util

        dev = _find(usb_core, idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise DeviceNotFoundError(
                f"no OpenPulse box at {self.vid:#06x}:{self.pid:#06x} "
                f"(plugged in and flashed?)"
            )

        # Free interface 2 from any kernel driver; leave CDC (0, 1) to the OS.
        try:
            if dev.is_kernel_driver_active(VENDOR_IFACE):
                dev.detach_kernel_driver(VENDOR_IFACE)
        except (NotImplementedError, usb_core.USBError):
            pass
        try:
            dev.set_configuration()
        except usb_core.USBError:
            pass  # already configured by the OS

        try:
            usb_util.claim_interface(dev, VENDOR_IFACE)
        except usb_core.USBError as e:
            raise InterfaceClaimError(
                f"found the box but could not claim its vendor interface "
                f"(interface {VENDOR_IFACE}). On Windows: bind it to WinUSB/"
                f"libusbK once with Zadig. On Linux: add a udev rule or run with "
                f"privileges. ({e})"
            ) from e

        self._dev = dev
        self._open = True

    def close(self) -> None:
        if self._dev is not None and self._usb_util is not None:
            try:
                self._usb_util.release_interface(self._dev, VENDOR_IFACE)
            except Exception:
                pass
            try:
                self._usb_util.dispose_resources(self._dev)
            except Exception:
                pass
        self._dev = None
        self._open = False

    # -- io -------------------------------------------------------------- #
    def send_frame(self, frame: bytes) -> None:
        if not self._open or self._dev is None:
            raise TransportError("transport is not open")
        try:
            self._dev.write(EP_OUT, frame, timeout=1000)
        except Exception as e:
            self._stats.send_errors += 1
            raise TransportError(f"USB write failed: {e}") from e
        self._stats.frames_sent += 1
        self._stats.bytes_sent += len(frame)

    def read_frame(self, timeout: float) -> bytes | None:
        if not self._open or self._dev is None:
            raise TransportError("transport is not open")
        import usb.core

        try:
            data = self._dev.read(EP_IN, _MAX_PACKET, timeout=max(1, int(timeout * 1000)))
        except usb.core.USBError as e:
            # libusb reports a plain timeout as errno 110 (LIBUSB_ERROR_TIMEOUT)
            # or with "timeout" in the message; that is the normal idle path.
            if e.errno in (110, None) or "timed out" in str(e).lower() or "timeout" in str(e).lower():
                return None
            self._stats.recv_errors += 1
            raise TransportError(f"USB read failed: {e}") from e
        buf = bytes(data)
        if not buf:
            return None
        self._stats.frames_recv += 1
        self._stats.bytes_recv += len(buf)
        return buf

    @property
    def stats(self) -> TransportStats:
        return self._stats
