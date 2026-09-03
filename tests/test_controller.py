"""BoxController against the mock transport (no GUI, no hardware)."""

import time

from openpulse.config import AppSettings
from openpulse.controller import BoxController
from openpulse.transport import protocol as P


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_connect_stream_ping_disconnect():
    ctl = BoxController(AppSettings(transport="mock"))
    ctl.connect_async()
    assert _wait(lambda: ctl.snapshot().status == "connected")
    snap = ctl.snapshot()
    assert snap.fw == "0.2" and snap.proto_ok and snap.transport_kind == "mock"

    ctl.start_stream()
    assert _wait(lambda: ctl.snapshot().streaming and ctl.snapshot().samples_sent > 200)
    assert _wait(lambda: (s := ctl.snapshot().stats) is not None and s.mode == P.MODE_SAMPLES)

    ctl.ping_burst(10)
    assert _wait(lambda: len(ctl.snapshot().rtt_ms) > 0)

    ctl.stop_stream()
    ctl.disconnect()
    assert _wait(lambda: ctl.snapshot().status == "disconnected")


def test_error_status_on_bad_usb(tmp_path):
    # usb transport with a VID/PID that will not be found -> error, not crash
    ctl = BoxController(AppSettings(transport="usb", usb_vid=0x0000, usb_pid=0x0000))
    ctl.connect_async()
    assert _wait(lambda: ctl.snapshot().status == "error")
    assert ctl.snapshot().detail
