"""Phase-A GUI: Connection + Diagnostics, built on dearpygui.

The controller runs the real work on worker threads; this module only reads
``controller.snapshot()`` once per rendered frame and pushes values into widgets.
All dearpygui calls happen on this (main) thread.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from openpulse import __version__
from openpulse.config import AppSettings
from openpulse.controller import BoxController
from openpulse.transport import protocol as P
from openpulse.transport.usb import list_devices
from openpulse.waveforms import WAVEFORMS

_STAT_ROWS = [
    ("uptime_s", "uptime s"),
    ("frames_rx", "frames rx"),
    ("samples_played", "samples played"),
    ("fifo_fill", "fifo fill"),
    ("fifo_overrun", "fifo overrun"),
    ("fifo_underrun", "fifo underrun"),
    ("tick_backlog_max", "tick backlog max"),
    ("crc_err", "crc err"),
    ("seq_gap_frames", "seq gap frames"),
    ("resync", "resync"),
    ("bad_type", "bad type"),
    ("i2c_err", "i2c err"),
    ("failsafe_trips", "failsafe trips"),
]


class OpenPulseGui:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()
        self.controller = BoxController(self.settings)

    # -- callbacks --------------------------------------------------------- #
    def _apply_settings_from_widgets(self) -> None:
        s = self.settings
        s.transport = dpg.get_value("cfg_transport")
        try:
            s.usb_vid = int(dpg.get_value("cfg_vid"), 0)
            s.usb_pid = int(dpg.get_value("cfg_pid"), 0)
        except ValueError:
            pass
        s.device.sample_rate_hz = int(dpg.get_value("cfg_rate"))
        s.device.failsafe_ms = int(dpg.get_value("cfg_failsafe"))
        s.stream.waveform = dpg.get_value("cfg_wave")

    def _on_connect(self) -> None:
        self._apply_settings_from_widgets()
        self.controller.connect_async()

    def _on_disconnect(self) -> None:
        self.controller.disconnect()

    def _on_list(self) -> None:
        try:
            devs = list_devices(self.settings.usb_vid)
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            dpg.set_value("dev_list", f"error: {e}")
            return
        dpg.set_value("dev_list", "\n".join(str(d) for d in devs) or "(none)")

    def _on_start(self) -> None:
        self._apply_settings_from_widgets()
        self.controller.start_stream()

    def _on_stop(self) -> None:
        self.controller.stop_stream()

    def _on_ping(self) -> None:
        self.controller.ping_burst(50)

    # -- build ---------------------------------------------------------- #
    def build(self) -> None:
        with dpg.window(tag="connection", label="Connection", pos=(8, 8), width=460, height=360):
            dpg.add_radio_button(
                ("usb", "mock"), tag="cfg_transport", horizontal=True,
                default_value=self.settings.transport,
            )
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="cfg_vid", label="VID", width=90,
                                   default_value=f"0x{self.settings.usb_vid:04X}")
                dpg.add_input_text(tag="cfg_pid", label="PID", width=90,
                                   default_value=f"0x{self.settings.usb_pid:04X}")
            with dpg.group(horizontal=True):
                dpg.add_input_int(tag="cfg_rate", label="sample_rate_hz", width=120,
                                  default_value=self.settings.device.sample_rate_hz,
                                  min_value=P.PROTO_RATE_MIN, max_value=P.PROTO_RATE_MAX,
                                  min_clamped=True, max_clamped=True, step=0)
                dpg.add_input_int(tag="cfg_failsafe", label="failsafe_ms", width=120,
                                  default_value=self.settings.device.failsafe_ms,
                                  min_value=P.PROTO_FAILSAFE_MIN, max_value=P.PROTO_FAILSAFE_MAX,
                                  min_clamped=True, max_clamped=True, step=0)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Connect", callback=self._on_connect)
                dpg.add_button(label="Disconnect", callback=self._on_disconnect)
                dpg.add_button(label="List USB", callback=self._on_list)
            dpg.add_separator()
            dpg.add_text("", tag="conn_status")
            dpg.add_text("", tag="conn_detail", wrap=440)
            dpg.add_text("", tag="conn_fw")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_combo(tuple(WAVEFORMS), tag="cfg_wave", width=110,
                              default_value=self.settings.stream.waveform)
                dpg.add_button(label="Start stream", callback=self._on_start)
                dpg.add_button(label="Stop stream", callback=self._on_stop)
            dpg.add_text("", tag="stream_status")
            dpg.add_separator()
            dpg.add_text("USB devices:", color=(150, 150, 150))
            dpg.add_text("(click List USB)", tag="dev_list", wrap=440, color=(150, 150, 150))

        with dpg.window(tag="diag", label="Diagnostics", pos=(476, 8), width=496, height=640):
            with dpg.table(header_row=True, tag="stat_table", policy=dpg.mvTable_SizingFixedFit):
                dpg.add_table_column(label="counter")
                dpg.add_table_column(label="value")
                for key, lbl in _STAT_ROWS:
                    with dpg.table_row():
                        dpg.add_text(lbl)
                        dpg.add_text("-", tag=f"stat_{key}")
            dpg.add_separator()
            dpg.add_text("-", tag="stat_drv")
            dpg.add_text("-", tag="stat_rtt")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Ping x50", callback=self._on_ping)
            dpg.add_separator()
            dpg.add_text("Faults", color=(230, 160, 90))
            with dpg.child_window(height=90, tag="fault_box"):
                dpg.add_text("(none)", tag="fault_text", wrap=470)
            dpg.add_text("Box log", color=(150, 150, 150))
            with dpg.child_window(height=220, tag="log_box"):
                dpg.add_text("(none)", tag="log_text", wrap=470)

    # -- per-frame update --------------------------------------------- #
    def update(self) -> None:
        snap = self.controller.snapshot()
        color = {
            "disconnected": (150, 150, 150),
            "connecting": (230, 200, 90),
            "connected": (120, 210, 120),
            "streaming": (120, 210, 120),
            "error": (230, 110, 110),
        }.get(snap.status, (200, 200, 200))
        dpg.set_value("conn_status", f"status: {snap.status}  [{snap.transport_kind}]")
        dpg.configure_item("conn_status", color=color)
        dpg.set_value("conn_detail", snap.detail)
        if snap.fw:
            ok = "OK" if snap.proto_ok else "MISMATCH"
            dpg.set_value("conn_fw", f"fw {snap.fw}  ·  proto v{P.PROTO_VERSION} {ok}  ·  "
                                     f"mode {P.MODE_NAME.get(snap.mode, snap.mode)}")
        else:
            dpg.set_value("conn_fw", "")
        dpg.set_value(
            "stream_status",
            f"streaming: {snap.streaming}   sent={snap.samples_sent}   "
            f"frames out={snap.frames_out} in={snap.frames_in}",
        )

        st = snap.stats
        for key, _ in _STAT_ROWS:
            dpg.set_value(f"stat_{key}", str(getattr(st, key)) if st else "-")
        if st:
            flags = []
            if st.over_temp:
                flags.append("OVER_TEMP")
            if st.over_current:
                flags.append("OC")
            dpg.set_value(
                "stat_drv",
                f"DRV status=0x{st.drv_status:02X} {' '.join(flags) or 'ok'}   VBAT~{st.vbat_v:.2f} V",
            )
            dpg.configure_item("stat_drv", color=(230, 110, 110) if flags else (200, 200, 200))
        else:
            dpg.set_value("stat_drv", "-")

        if snap.rtt_ms:
            r = sorted(snap.rtt_ms)
            p = lambda q: r[min(len(r) - 1, int(q * len(r)))]  # noqa: E731
            dpg.set_value(
                "stat_rtt",
                f"RTT ms  n={len(r)}  min={r[0]:.2f}  p50={p(0.5):.2f}  "
                f"p95={p(0.95):.2f}  last={snap.rtt_ms[-1]:.2f}",
            )
        else:
            dpg.set_value("stat_rtt", "RTT ms  (ping to measure)")

        dpg.set_value("fault_text", "\n".join(snap.faults[-8:]) or "(none)")
        dpg.set_value("log_text", "\n".join(snap.logs[-40:]) or "(none)")

    # -- run ---------------------------------------------------------- #
    def run(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title=f"OpenPulse {__version__}", width=996, height=680)
        dpg.setup_dearpygui()
        self.build()
        dpg.show_viewport()
        try:
            while dpg.is_dearpygui_running():
                self.update()
                dpg.render_dearpygui_frame()
        finally:
            self.controller.disconnect()
            dpg.destroy_context()


def run(settings: AppSettings | None = None) -> None:
    OpenPulseGui(settings).run()
