"""GUI smoke test — builds the window and renders frames against the mock box.

Skipped where no display / GPU context is available (headless CI).
"""

import inspect
import time

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

from openpulse.config import AppSettings  # noqa: E402
from openpulse.gui.app import OpenPulseGui  # noqa: E402


def _dpg_dispatch(tag, app_data):
    """Invoke a widget callback the way dearpygui's runner does: build args from
    (sender, app_data, user_data), truncated/padded to the callback's parameter
    count — so extra params (incl. default args) are overridden with None."""
    cb = dpg.get_item_callback(tag)
    job = [tag, app_data, dpg.get_item_user_data(tag)]
    n = len(inspect.signature(cb).parameters)
    cb(*[job[i] if i < len(job) else None for i in range(n)])


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
        for i in range(300):
            gui.update()
            dpg.render_dearpygui_frame()
            if i == 120:
                gui._on_engine()          # full effect engine -> mock box
            if i == 200:
                gui.settings.engine.effects.grip_scrub.gain = 2.0
        box = gui.controller.snapshot()
        tel = gui.telem.snapshot()
        pipe = gui.controller.pipeline()
        assert box.status in ("connected", "streaming")
        assert box.fw == "0.2"
        assert tel.status == "live" and tel.frame is not None
        assert tel.frame.grip_scrub == tel.frame.grip_scrub  # not NaN
        assert pipe is not None
        m = pipe.meters()
        assert 0.0 <= m.output_peak <= 1.0 and m.blocks > 10

        # a real checkbox click routes through dearpygui's arg-count trampoline,
        # which overrides default args with None — the callback must survive that
        # and actually flip the shared param model (regression: it did not).
        eff = gui.settings.engine.effects
        assert eff.road.enabled is True
        dpg.set_value("fx_road__enabled", False)
        _dpg_dispatch("fx_road__enabled", False)
        assert eff.road.enabled is False
        for _ in range(30):
            gui.update()
            dpg.render_dearpygui_frame()
        assert pipe.meters().effect_levels["road"] == 0.0

        # a param slider must write through too
        _dpg_dispatch("fx_grip_scrub__ceiling", 0.9)
        assert eff.grip_scrub.ceiling == pytest.approx(0.9)

        # the Test button (road is unchecked above) plays the effect solo anyway
        btn = next(i for i in dpg.get_all_items()
                   if dpg.get_item_user_data(i) == "rev_limiter"
                   and dpg.get_item_type(i) == "mvAppItemType::mvButton")
        _dpg_dispatch(btn, None)
        seen = 0.0
        for _ in range(40):
            gui.update()
            dpg.render_dearpygui_frame()
            seen = max(seen, pipe.meters().effect_levels.get("rev_limiter", 0.0))
        assert seen > 0.05
    finally:
        gui.controller.disconnect()
        gui.telem.stop()
        time.sleep(0.05)
        dpg.destroy_context()
