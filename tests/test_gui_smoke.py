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
        gui.controller.connect_async()
        for i in range(180):
            gui.update()
            dpg.render_dearpygui_frame()
            if i == 90:
                gui.controller.start_stream()
        snap = gui.controller.snapshot()
        assert snap.status in ("connected", "streaming")
        assert snap.fw == "0.2"
    finally:
        gui.controller.disconnect()
        time.sleep(0.05)
        dpg.destroy_context()
