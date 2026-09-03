"""GUI smoke test — builds the window and renders frames against the mock box.

Skipped where no display / GPU context is available (headless CI).
"""

import time

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

from openpulse.config import AppSettings  # noqa: E402
from openpulse.gui.app import OpenPulseGui  # noqa: E402


def test_gui_builds_and_streams():
    gui = OpenPulseGui(AppSettings(transport="mock"))
    try:
        dpg.create_context()
        dpg.create_viewport(title="test", width=400, height=300)
        dpg.setup_dearpygui()
    except Exception as e:  # pragma: no cover - environment dependent
        dpg.destroy_context()
        pytest.skip(f"no dearpygui viewport available: {e}")

    try:
        gui.build()
        dpg.show_viewport()
        gui.settings.telemetry.source = "synthetic"
        gui.settings.telemetry.scenario = "grip_scrub"
        dpg.set_value("tl_source", "synthetic")
        dpg.set_value("tl_scenario", "grip_scrub")
        dpg.set_value("tl_bridge_map", "grip_scrub")
        gui.controller.connect_async()
        gui._on_telem_start()
        for i in range(240):
            gui.update()
            dpg.render_dearpygui_frame()
            if i == 120:
                gui._on_bridge()
        box = gui.controller.snapshot()
        tel = gui.telem.snapshot()
        assert box.status in ("connected", "streaming")
        assert box.fw == "0.2"
        assert tel.status == "live" and tel.frame is not None
        assert tel.frame.grip_scrub == tel.frame.grip_scrub  # not NaN
    finally:
        gui.controller.disconnect()
        gui.telem.stop()
        time.sleep(0.05)
        dpg.destroy_context()
