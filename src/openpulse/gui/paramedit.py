"""Auto-generate dearpygui editors from a pydantic model.

One slider per numeric field (bounds from the Field's ge/le when present),
one checkbox per bool. The callback writes straight back into the model
instance the running Pipeline holds, so edits apply live.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg


def _bounds(field_info, value: float) -> tuple[float, float]:
    lo = hi = None
    for m in getattr(field_info, "metadata", []):
        for attr, name in (("ge", "lo"), ("gt", "lo"), ("le", "hi"), ("lt", "hi")):
            v = getattr(m, attr, None)
            if v is not None:
                if name == "lo":
                    lo = float(v)
                else:
                    hi = float(v)
    if lo is None:
        lo = min(0.0, float(value))
    if hi is None:
        hi = max(float(value) * 2.5, lo + 1.0)
    return lo, hi


def build_param_editor(model, tag_prefix: str, *, skip: tuple[str, ...] = ("enabled",), width: int = 240) -> None:
    fields = type(model).model_fields
    for name, fi in fields.items():
        if name in skip:
            continue
        val = getattr(model, name)
        tag = f"{tag_prefix}__{name}"

        def _cb(sender, app_data, user_data, _m=model, _n=name, _t=tag):
            try:
                setattr(_m, _n, dpg.get_value(_t))
            except Exception:
                dpg.set_value(_t, getattr(_m, _n))  # revert on validation error

        if isinstance(val, bool):
            dpg.add_checkbox(label=name, tag=tag, default_value=val, callback=_cb)
        elif isinstance(val, (int, float)):
            lo, hi = _bounds(fi, float(val))
            dpg.add_slider_float(
                label=name, tag=tag, default_value=float(val),
                min_value=lo, max_value=hi, width=width, callback=_cb, clamped=True,
            )
        elif isinstance(val, str):
            dpg.add_input_text(label=name, tag=tag, default_value=val, width=width, callback=_cb)


def refresh_param_editor(model, tag_prefix: str, *, skip: tuple[str, ...] = ("enabled",)) -> None:
    """Push current model values back into the widgets (after a profile load)."""
    for name in type(model).model_fields:
        if name in skip:
            continue
        tag = f"{tag_prefix}__{name}"
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, getattr(model, name))
