"""Phase-B GUI: Connection + Diagnostics + Telemetry monitor, built on dearpygui.

Two controllers run the real work on worker threads (:class:`BoxController`,
:class:`TelemetryController`); this module only reads their snapshots once per
rendered frame and pushes values into widgets. All dearpygui calls happen on
this (main) thread.
"""

from __future__ import annotations

import math

import dearpygui.dearpygui as dpg

from openpulse import __version__
from openpulse.config import AppSettings
from openpulse.controller import BoxController
from openpulse.debugmap import DEBUG_MAPS
from openpulse.engine.registry import EFFECT_CLASSES
from openpulse.gui.paramedit import build_param_editor
from openpulse.telemetry.controller import TelemetryController
from openpulse.telemetry.frame import WHEELS, Cap
from openpulse.telemetry.registry import SOURCE_KINDS
from openpulse.telemetry.synthetic import SCENARIOS
from openpulse.transport import protocol as P
from openpulse.transport.usb import list_devices
from openpulse.waveforms import WAVEFORMS

_STAT_ROWS = [
    ("uptime_s", "uptime s"), ("frames_rx", "frames rx"), ("samples_played", "samples played"),
    ("fifo_fill", "fifo fill"), ("fifo_overrun", "fifo overrun"), ("fifo_underrun", "fifo underrun"),
    ("tick_backlog_max", "tick backlog max"), ("crc_err", "crc err"), ("seq_gap_frames", "seq gap"),
    ("resync", "resync"), ("bad_type", "bad type"), ("i2c_err", "i2c err"), ("failsafe_trips", "failsafe trips"),
]
_WHEEL_ROWS = [
    ("wheel_omega", "omega rad/s"), ("lock_ratio", "lock ratio"), ("slip_ratio", "slip ratio"),
    ("combined_slip", "combined slip"), ("nd_slip", "nd slip"), ("susp_travel_m", "susp travel m"),
    ("susp_vel_ms", "susp vel m/s"), ("load_n", "load N"), ("tyre_temp_c", "tyre temp C"),
]


def _fmt(x: float) -> str:
    return "  -  " if not isinstance(x, (int, float)) or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


class OpenPulseGui:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()
        self.controller = BoxController(self.settings)
        self.telem = TelemetryController(self.settings.telemetry.derive)
        self._bridge_on = False

    # -- settings <- widgets --------------------------------------------- #
    def _apply_box_widgets(self) -> None:
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

    def _apply_telem_widgets(self) -> None:
        t = self.settings.telemetry
        t.source = dpg.get_value("tl_source")
        t.udp_host = dpg.get_value("tl_host")
        t.udp_port = int(dpg.get_value("tl_port"))
        t.scenario = dpg.get_value("tl_scenario")
        t.replay_path = dpg.get_value("tl_replay")
        t.rpm_max_hint = float(dpg.get_value("tl_rpmmax"))

    # -- callbacks ---------------------------------------------------------- #
    def _on_connect(self) -> None:
        self._apply_box_widgets()
        self.controller.connect_async()

    def _on_list(self) -> None:
        try:
            devs = list_devices(self.settings.usb_vid)
        except Exception as e:  # noqa: BLE001
            dpg.set_value("dev_list", f"error: {e}")
            return
        dpg.set_value("dev_list", "\n".join(str(d) for d in devs) or "(none)")

    def _on_start_wave(self) -> None:
        self._apply_box_widgets()
        self.controller.start_stream()

    def _on_stop_stream(self) -> None:
        self._bridge_on = False
        self.controller.stop_stream()

    def _on_ping(self) -> None:
        self.controller.ping_burst(50)

    def _on_telem_start(self) -> None:
        self._apply_telem_widgets()
        t = self.settings.telemetry
        self.telem.start(
            t.source, udp_host=t.udp_host, udp_port=t.udp_port,
            replay_path=t.replay_path, scenario=t.scenario,
            poll_hz=t.poll_hz, rpm_max_hint=t.rpm_max_hint,
        )

    def _on_telem_stop(self) -> None:
        self._bridge_on = False
        self.telem.stop()

    def _on_record(self) -> None:
        path = dpg.get_value("tl_recpath") or "recordings/capture.jsonl"
        self.telem.start_recording(path)

    def _on_record_stop(self) -> None:
        self.telem.stop_recording()

    def _bridge_amp_fn(self):  # noqa: ANN202
        return DEBUG_MAPS.get(dpg.get_value("tl_bridge_map"), DEBUG_MAPS["off"])

    def _on_bridge(self) -> None:
        if self._bridge_on:
            return
        self._bridge_on = True
        self.controller.start_telemetry_bridge(self.telem.latest_frame, self._bridge_amp_fn())

    def _on_engine(self) -> None:
        self._bridge_on = False
        self.controller.start_engine(self.telem.latest_frame)

    # -- build ---------------------------------------------------------- #
    def build(self) -> None:
        self._build_connection()
        self._build_diagnostics()
        self._build_telemetry()
        self._build_effects()
        self._build_mixer()

    def _build_connection(self) -> None:
        with dpg.window(tag="connection", label="Connection (box)", pos=(8, 8), width=460, height=330):
            dpg.add_radio_button(("usb", "mock"), tag="cfg_transport", horizontal=True,
                                 default_value=self.settings.transport)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="cfg_vid", label="VID", width=90,
                                   default_value=f"0x{self.settings.usb_vid:04X}")
                dpg.add_input_text(tag="cfg_pid", label="PID", width=90,
                                   default_value=f"0x{self.settings.usb_pid:04X}")
            with dpg.group(horizontal=True):
                dpg.add_input_int(tag="cfg_rate", label="sample_rate_hz", width=120, step=0,
                                  default_value=self.settings.device.sample_rate_hz,
                                  min_value=P.PROTO_RATE_MIN, max_value=P.PROTO_RATE_MAX,
                                  min_clamped=True, max_clamped=True)
                dpg.add_input_int(tag="cfg_failsafe", label="failsafe_ms", width=120, step=0,
                                  default_value=self.settings.device.failsafe_ms,
                                  min_value=P.PROTO_FAILSAFE_MIN, max_value=P.PROTO_FAILSAFE_MAX,
                                  min_clamped=True, max_clamped=True)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Connect", callback=self._on_connect)
                dpg.add_button(label="Disconnect", callback=lambda: self.controller.disconnect())
                dpg.add_button(label="List USB", callback=self._on_list)
            dpg.add_separator()
            dpg.add_text("", tag="conn_status")
            dpg.add_text("", tag="conn_detail", wrap=440)
            dpg.add_text("", tag="conn_fw")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_combo(tuple(WAVEFORMS), tag="cfg_wave", width=100,
                              default_value=self.settings.stream.waveform)
                dpg.add_button(label="Stream waveform", callback=self._on_start_wave)
                dpg.add_button(label="Stop", callback=self._on_stop_stream)
            dpg.add_text("", tag="stream_status", wrap=440)
            dpg.add_text("(click List USB)", tag="dev_list", wrap=440, color=(150, 150, 150))

    def _build_diagnostics(self) -> None:
        with dpg.window(tag="diag", label="Diagnostics (box)", pos=(476, 8), width=430, height=610):
            with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit):
                dpg.add_table_column(label="counter")
                dpg.add_table_column(label="value")
                for key, lbl in _STAT_ROWS:
                    with dpg.table_row():
                        dpg.add_text(lbl)
                        dpg.add_text("-", tag=f"stat_{key}")
            dpg.add_separator()
            dpg.add_text("-", tag="stat_drv")
            dpg.add_text("-", tag="stat_rtt")
            dpg.add_button(label="Ping x50", callback=self._on_ping)
            dpg.add_separator()
            dpg.add_text("Faults", color=(230, 160, 90))
            with dpg.child_window(height=80):
                dpg.add_text("(none)", tag="fault_text", wrap=400)
            dpg.add_text("Box log", color=(150, 150, 150))
            with dpg.child_window(height=180):
                dpg.add_text("(none)", tag="log_text", wrap=400)

    def _build_telemetry(self) -> None:
        with dpg.window(tag="telemetry", label="Telemetry", pos=(8, 346), width=898, height=468):
            with dpg.group(horizontal=True):
                dpg.add_combo(SOURCE_KINDS, tag="tl_source", width=110,
                              default_value=self.settings.telemetry.source)
                dpg.add_input_text(tag="tl_host", label="UDP host", width=130,
                                   default_value=self.settings.telemetry.udp_host)
                dpg.add_input_int(tag="tl_port", label="port", width=90, step=0,
                                  default_value=self.settings.telemetry.udp_port)
                dpg.add_combo(sorted(SCENARIOS), tag="tl_scenario", width=110,
                              default_value=self.settings.telemetry.scenario)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="tl_replay", label="replay", width=260,
                                   default_value=self.settings.telemetry.replay_path)
                dpg.add_input_float(tag="tl_rpmmax", label="rpm_max hint (UDP)", width=120,
                                    default_value=self.settings.telemetry.rpm_max_hint, step=0)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Start telemetry", callback=self._on_telem_start)
                dpg.add_button(label="Stop", callback=self._on_telem_stop)
                dpg.add_input_text(tag="tl_recpath", width=220, default_value="recordings/capture.jsonl")
                dpg.add_button(label="Record", callback=self._on_record)
                dpg.add_button(label="Stop rec", callback=self._on_record_stop)
            dpg.add_text("", tag="tl_status")
            dpg.add_text("", tag="tl_session", color=(150, 150, 150), wrap=880)
            dpg.add_text("", tag="tl_caps", color=(150, 150, 150), wrap=880)
            dpg.add_separator()

            with dpg.group(horizontal=True):
                with dpg.group(width=250):
                    dpg.add_text("-", tag="tl_speed")
                    dpg.add_text("-", tag="tl_gear")
                    for lbl, tag in (("throttle", "tl_thr"), ("brake", "tl_brk"),
                                     ("rpm", "tl_rpm"), ("grip scrub", "tl_scrub")):
                        dpg.add_text(lbl)
                        dpg.add_progress_bar(tag=tag, width=230)
                    dpg.add_text("-", tag="tl_scrub_fr")
                    dpg.add_text("-", tag="tl_derived", wrap=250)
                    dpg.add_text("-", tag="tl_flags", wrap=250)
                with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit):
                    dpg.add_table_column(label="metric")
                    for w in WHEELS:
                        dpg.add_table_column(label=w)
                    for key, lbl in _WHEEL_ROWS:
                        with dpg.table_row():
                            dpg.add_text(lbl)
                            for i in range(4):
                                dpg.add_text("-", tag=f"tw_{key}_{i}")

            dpg.add_separator()
            with dpg.collapsing_header(label="Derive thresholds (grip_scrub, impact, lockup, ABS/TC...)"):
                dpg.add_text(
                    "grip_scrub minimum: scrub_wheelslip_lo (shared memory) / scrub_nd_lo (UDP) below.",
                    color=(150, 150, 150), wrap=860,
                )
                build_param_editor(self.settings.telemetry.derive, "dv", skip=(), width=210)
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_text("-> box:")
                dpg.add_button(label="Effect engine", callback=self._on_engine)
                dpg.add_text("   or debug bridge:")
                dpg.add_combo(list(DEBUG_MAPS), tag="tl_bridge_map", width=130, default_value="grip_scrub")
                dpg.add_button(label="one scalar", callback=self._on_bridge)
                dpg.add_button(label="Stop", callback=self._on_stop_stream)
            dpg.add_progress_bar(tag="tl_bridge_amp", width=400)

    def _build_effects(self) -> None:
        eff = self.settings.engine.effects
        with dpg.window(tag="effects", label="Effects", pos=(916, 8), width=390, height=560):
            dpg.add_text("value after each name = live contribution meter (0..1)",
                         color=(150, 150, 150), wrap=370)
            for cls in EFFECT_CLASSES:
                model = getattr(eff, cls.id)
                with dpg.collapsing_header(label=f"{cls.id}  ({cls.kind})", tag=f"fx_hdr_{cls.id}"):
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(
                            label="on", tag=f"fx_{cls.id}__enabled", default_value=model.enabled,
                            callback=lambda s, a, u, m=model, t=f"fx_{cls.id}__enabled": setattr(
                                m, "enabled", dpg.get_value(t)),
                        )
                        dpg.add_button(label="Test", callback=lambda s, a, u, cid=cls.id:
                                       self.controller.trigger_effect_test(cid))
                        dpg.add_text("-", tag=f"fx_meter_{cls.id}")
                    build_param_editor(model, f"fx_{cls.id}", width=210)

    def _build_mixer(self) -> None:
        with dpg.window(tag="mixer", label="Mixer / output", pos=(916, 576), width=380, height=310):
            dpg.add_checkbox(
                label="bench mode (run even when not on track)", tag="mx_force",
                default_value=self.settings.engine.force_enable,
                callback=lambda: setattr(self.settings.engine, "force_enable", dpg.get_value("mx_force")),
            )
            build_param_editor(self.settings.engine.mixer, "mx", skip=(), width=210)
            dpg.add_separator()
            dpg.add_text("-", tag="mx_status", wrap=360)
            dpg.add_text("output")
            dpg.add_progress_bar(tag="mx_vu", width=340)
            dpg.add_slider_float(
                label="scope window (s)", tag="mx_scope_window", width=210,
                default_value=2.0, min_value=0.3, max_value=10.0, clamped=True,
            )
            with dpg.plot(tag="mx_scope", height=110, width=340, no_menus=True):
                dpg.add_plot_axis(dpg.mvXAxis, no_tick_labels=True, tag="mx_x")
                dpg.add_plot_axis(dpg.mvYAxis, tag="mx_y")
                dpg.set_axis_limits("mx_y", 0.0, 1.0)
                dpg.add_line_series([], [], parent="mx_y", tag="mx_series")

    # -- per-frame update --------------------------------------------- #
    def update(self) -> None:
        self._update_box()
        self._update_telem()
        self._update_engine()
        if self._bridge_on:
            self.controller.set_bridge_amp_fn(self._bridge_amp_fn())

    def _update_box(self) -> None:
        snap = self.controller.snapshot()
        color = {
            "disconnected": (150, 150, 150), "connecting": (230, 200, 90),
            "connected": (120, 210, 120), "streaming": (120, 210, 120), "error": (230, 110, 110),
        }.get(snap.status, (200, 200, 200))
        dpg.set_value("conn_status", f"status: {snap.status}  [{snap.transport_kind}]")
        dpg.configure_item("conn_status", color=color)
        dpg.set_value("conn_detail", snap.detail)
        dpg.set_value(
            "conn_fw",
            f"fw {snap.fw}  ·  proto v{P.PROTO_VERSION} {'OK' if snap.proto_ok else 'MISMATCH'}  ·  "
            f"mode {P.MODE_NAME.get(snap.mode, snap.mode)}" if snap.fw else "",
        )
        ss = (f"stream: {snap.stream_kind or 'idle'}  sent={snap.samples_sent}  "
              f"out={snap.frames_out} in={snap.frames_in}")
        if snap.stream_error:
            ss += f"\n!! streamer error: {snap.stream_error}"
        dpg.set_value("stream_status", ss)
        dpg.configure_item("stream_status", color=(230, 110, 110) if snap.stream_error else (200, 200, 200))
        st = snap.stats
        for key, _ in _STAT_ROWS:
            dpg.set_value(f"stat_{key}", str(getattr(st, key)) if st else "-")
        if st:
            fl = [n for n, on in (("OVER_TEMP", st.over_temp), ("OC", st.over_current)) if on]
            dpg.set_value("stat_drv", f"DRV 0x{st.drv_status:02X} {' '.join(fl) or 'ok'}  VBAT~{st.vbat_v:.2f} V")
            dpg.configure_item("stat_drv", color=(230, 110, 110) if fl else (200, 200, 200))
        if snap.rtt_ms:
            r = sorted(snap.rtt_ms)
            p = r[min(len(r) - 1, int(0.5 * len(r)))]
            dpg.set_value("stat_rtt", f"RTT ms  n={len(r)}  min={r[0]:.2f}  p50={p:.2f}  last={snap.rtt_ms[-1]:.2f}")
        dpg.set_value("fault_text", "\n".join(snap.faults[-8:]) or "(none)")
        dpg.set_value("log_text", "\n".join(snap.logs[-30:]) or "(none)")
        dpg.set_value("tl_bridge_amp", snap.bridge_amp if math.isfinite(snap.bridge_amp) else 0.0)

    def _update_telem(self) -> None:
        s = self.telem.snapshot()
        col = {"offline": (150, 150, 150), "connecting": (230, 200, 90),
               "connected": (120, 210, 120), "live": (120, 210, 120), "error": (230, 110, 110)}
        dpg.set_value("tl_status", f"{s.status}  [{s.source_active or s.source_kind}]  "
                                   f"{s.rate_hz:.0f} Hz  frames={s.frames_seen}  "
                                   f"{'REC ' + str(s.record_count) if s.recording else ''}  {s.detail}")
        dpg.configure_item("tl_status", color=col.get(s.status, (200, 200, 200)))
        dpg.set_value("tl_session", s.session.label() if s.session else "(no session)")
        have = [c.name for c in Cap if c != Cap.NONE and c in s.caps]
        dpg.set_value("tl_caps", "caps: " + (", ".join(have) or "(none)"))

        f = s.frame
        if f is None:
            return
        dpg.set_value("tl_speed", f"speed {f.speed_kmh:6.1f} km/h   steer {f.steer:+.2f}")
        dpg.set_value("tl_gear", f"gear {f.gear:+d}   rpm {f.rpm:.0f} / {f.rpm_max:.0f}")
        dpg.set_value("tl_thr", f.throttle)
        dpg.set_value("tl_brk", f.brake)
        dpg.set_value("tl_rpm", (f.rpm / f.rpm_max) if f.rpm_max > 0 else 0.0)
        dpg.set_value("tl_scrub", f.grip_scrub if math.isfinite(f.grip_scrub) else 0.0)
        dpg.set_value("tl_scrub_fr", f"scrub  front {_fmt(f.grip_scrub_front)}  rear {_fmt(f.grip_scrub_rear)}")
        dpg.set_value(
            "tl_derived",
            f"jerk {_fmt(f.jerk_g_s)} g/s   impact {_fmt(f.impact_g)}   "
            f"gear_change {f.gear_change:+d}   dt {f.dt * 1000:.1f} ms",
        )
        flags = [n for n, on in (
            ("on_track", f.on_track), ("pit", f.in_pit), ("ABS", f.abs_active), ("TC", f.tc_active),
            ("rev_lim", f.rev_limiter), ("DRS", f.drs_active),
        ) if on]
        dpg.set_value("tl_flags", "flags: " + (" ".join(flags) or "-") + f"   off-track {f.off_track_wheels}")
        for key, _ in _WHEEL_ROWS:
            vals = getattr(f, key)
            for i in range(4):
                dpg.set_value(f"tw_{key}_{i}", _fmt(vals[i]))

    def _update_engine(self) -> None:
        pipe = self.controller.pipeline()
        if pipe is None:
            dpg.set_value("mx_status", "engine not running — Connect the box, Start telemetry, "
                                       "then 'Effect engine'")
            dpg.set_value("mx_vu", 0.0)
            return
        m = pipe.meters()
        dpg.set_value("mx_vu", m.output_peak)
        why = ""
        if m.enable < 0.05:
            f = self.telem.snapshot().frame
            if not m.telemetry_fresh:
                why = "  <- no fresh telemetry"
            elif f is not None and not f.on_track:
                why = "  <- car not on track (use bench mode to override)"
            else:
                why = "  <- output near zero (car stopped?)"
        dpg.set_value(
            "mx_status",
            f"blocks {m.blocks}  peak {m.output_peak:.2f}  rms {m.output_rms:.2f}  "
            f"enable {m.enable:.2f}{why}\n"
            f"duck {m.duck_gain:.2f}  gate {m.gate_gain:.2f}  "
            f"thermal {m.thermal_gain:.2f} (E {m.thermal_energy:.3f})"
            + ("" if m.telemetry_fresh else "  [telemetry stale]"),
        )
        for cid, lvl in m.effect_levels.items():
            if dpg.does_item_exist(f"fx_meter_{cid}"):
                dpg.set_value(f"fx_meter_{cid}", f"{lvl:.2f}")
        window_s = dpg.get_value("mx_scope_window")
        hist = pipe.history(seconds=window_s)
        if hist:
            t0 = hist[0][0]
            xs = [t - t0 for t, _ in hist]
            ys = [v for _, v in hist]
            dpg.set_value("mx_series", [xs, ys])
            dpg.set_axis_limits("mx_x", 0.0, max(window_s, xs[-1]))

    # -- run ---------------------------------------------------------- #
    def run(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title=f"OpenPulse {__version__}", width=1320, height=860)
        dpg.setup_dearpygui()
        self.build()
        dpg.show_viewport()
        try:
            while dpg.is_dearpygui_running():
                self.update()
                dpg.render_dearpygui_frame()
        finally:
            self.controller.disconnect()
            self.telem.stop()
            dpg.destroy_context()


def run(settings: AppSettings | None = None) -> None:
    OpenPulseGui(settings).run()
