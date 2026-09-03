"""openpulse-cli — headless streamer, functional parity with
``OpenPulse-firmware/tools/pc_sender/openpulse_send.py``.

    openpulse-cli --list
    openpulse-cli --wave sim --duration 20
    openpulse-cli --transport mock --wave sine

Ctrl-C stops cleanly (SET_MODE IDLE + failsafe backstop).
"""

from __future__ import annotations

import argparse
import sys
import time

from openpulse.box.session import BoxSession, ProtocolMismatch
from openpulse.config import DeviceConfig, StreamConfig
from openpulse.streamer import SampleStreamer
from openpulse.transport import protocol as P
from openpulse.transport.base import TransportError
from openpulse.transport.mock import MockTransport
from openpulse.transport.usb import USB_PID, USB_VID, UsbVendorTransport, list_devices
from openpulse.waveforms import WAVEFORMS


def _dashboard(st: P.Stats | None) -> str:
    if st is None:
        return "  (no STATUS yet)"
    return (
        f"  up={st.uptime_s}s rx={st.frames_rx} played={st.samples_played} "
        f"fifo={st.fifo_fill} ovr={st.fifo_overrun} unr={st.fifo_underrun} "
        f"backlog={st.tick_backlog_max} crc={st.crc_err} gap={st.seq_gap_frames} "
        f"rsync={st.resync} bad={st.bad_type} i2c={st.i2c_err} fs={st.failsafe_trips} "
        f"STAT=0x{st.drv_status:02X} VBAT~{st.vbat_v:.2f}V"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list matching USB devices and exit")
    ap.add_argument("--transport", choices=("usb", "mock"), default="usb")
    ap.add_argument("--wave", choices=tuple(WAVEFORMS), default="sim")
    ap.add_argument("--rate", type=int, default=1000, help="sample rate Hz (250..4000)")
    ap.add_argument("--batch", type=int, default=8, help="samples per DATA_SAMPLES frame")
    ap.add_argument("--lookahead-ms", type=int, default=20)
    ap.add_argument("--failsafe", type=int, default=100, help="failsafe_ms")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds (0 = until Ctrl-C)")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=USB_VID)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=USB_PID)
    a = ap.parse_args(argv)

    if a.list:
        try:
            found = list_devices(a.vid)
        except TransportError as e:
            print(f"!! {e}", file=sys.stderr)
            return 1
        for d in found:
            print(" ", d)
        if not found:
            print("  (none)")
        return 0

    transport = MockTransport() if a.transport == "mock" else UsbVendorTransport(a.vid, a.pid)
    device = DeviceConfig(sample_rate_hz=a.rate, failsafe_ms=a.failsafe)
    stream = StreamConfig(waveform=a.wave, batch=a.batch, lookahead_ms=a.lookahead_ms)
    session = BoxSession(transport)

    try:
        hs = session.connect(device, reset_stats=True)
    except ProtocolMismatch as e:
        print(f"!! {e}", file=sys.stderr)
        return 2
    except (TransportError, TimeoutError) as e:
        print(f"!! could not connect ({a.transport}): {e}", file=sys.stderr)
        return 1
    print(f"connected: fw {hs.fw}, proto v{hs.proto_version}, mode {P.MODE_NAME.get(hs.mode, hs.mode)}")

    fn = WAVEFORMS[a.wave]
    streamer = SampleStreamer(session, fn, a.rate, stream)
    print(
        f"streaming '{a.wave}' at {a.rate} Hz, {a.batch}/frame, "
        f"{a.lookahead_ms} ms lookahead - Ctrl-C to stop"
    )
    streamer.start()

    t0 = time.perf_counter()
    try:
        while a.duration == 0.0 or (time.perf_counter() - t0) < a.duration:
            time.sleep(1.0)
            snap = session.snapshot()
            rtt = f"  rtt~{snap.rtt_ms[-1]:.2f}ms" if snap.rtt_ms else ""
            print(_dashboard(snap.last_stats) + rtt)
            for ln in list(snap.logs)[-3:]:
                print(f"  LOG/{ln.level_name}: {ln.text}")
            for ft in list(snap.faults)[-3:]:
                print(f"  !! FAULT {ft.name} detail={ft.detail}")
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        streamer.stop()
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
