"""Phase-B debug bridge: stream one telemetry-derived scalar straight to the box.

Lets you feel a single channel (grip scrub, lockup, rpm, ...) before the effect
engine exists.

    python -m openpulse.tools.bridge --map grip_scrub --telemetry auto
    python -m openpulse.tools.bridge --map lockup --telemetry ac-udp --host 192.168.1.20
    python -m openpulse.tools.bridge --map speed --telemetry synthetic --scenario hotlap --transport mock
"""

from __future__ import annotations

import argparse
import sys
import time

from openpulse.box.session import BoxSession, ProtocolMismatch
from openpulse.config import DeviceConfig, StreamConfig
from openpulse.debugmap import DEBUG_MAPS
from openpulse.streamer import TelemetryMapStreamer
from openpulse.telemetry.controller import TelemetryController
from openpulse.transport.base import TransportError
from openpulse.transport.mock import MockTransport
from openpulse.transport.usb import USB_PID, USB_VID, UsbVendorTransport


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", choices=tuple(DEBUG_MAPS), default="grip_scrub")
    ap.add_argument("--telemetry", choices=("auto", "ac-shm", "ac-udp", "synthetic", "replay"), default="auto")
    ap.add_argument("--transport", choices=("usb", "mock"), default="usb")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9996)
    ap.add_argument("--scenario", default="hotlap")
    ap.add_argument("--replay-path", default="")
    ap.add_argument("--rpm-max", type=float, default=0.0)
    ap.add_argument("--rate", type=int, default=1000)
    ap.add_argument("--failsafe", type=int, default=100)
    ap.add_argument("--duration", type=float, default=0.0)
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=USB_VID)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=USB_PID)
    a = ap.parse_args(argv)

    transport = MockTransport() if a.transport == "mock" else UsbVendorTransport(a.vid, a.pid)
    session = BoxSession(transport)
    try:
        hs = session.connect(DeviceConfig(sample_rate_hz=a.rate, failsafe_ms=a.failsafe), reset_stats=True)
    except ProtocolMismatch as e:
        print(f"!! {e}", file=sys.stderr)
        return 2
    except (TransportError, TimeoutError) as e:
        print(f"!! box: {e}", file=sys.stderr)
        return 1
    print(f"box: fw {hs.fw}, proto v{hs.proto_version}")

    telem = TelemetryController()
    telem.start(
        a.telemetry, udp_host=a.host, udp_port=a.port, replay_path=a.replay_path,
        scenario=a.scenario, rpm_max_hint=a.rpm_max,
    )
    time.sleep(0.3)
    print(f"telemetry: {telem.snapshot().status} [{telem.snapshot().source_active}]")

    streamer = TelemetryMapStreamer(
        session, telem.latest_frame, DEBUG_MAPS[a.map], a.rate, StreamConfig(batch=8, lookahead_ms=20)
    )
    streamer.start()
    print(f"bridging telemetry '{a.map}' -> box amplitude - Ctrl-C to stop")

    t0 = time.perf_counter()
    try:
        while a.duration == 0.0 or (time.perf_counter() - t0) < a.duration:
            time.sleep(1.0)
            ts = telem.snapshot()
            f = ts.frame
            extra = ""
            if f is not None:
                extra = f" speed={f.speed_kmh:.0f} scrub={f.grip_scrub:.2f}" if f.grip_scrub == f.grip_scrub else ""
            print(f"  amp={streamer.current_amp:.2f}  telem={ts.status} {ts.rate_hz:.0f}Hz{extra}")
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        streamer.stop()
        telem.stop()
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
