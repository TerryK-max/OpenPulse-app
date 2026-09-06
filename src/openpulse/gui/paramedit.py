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

        # dearpygui's callback runner counts the callback's parameters and fills
        # anything past (sender, app_data, user_data) with None — it *overrides*
        # default args. So the field/model reference must travel in user_data,
        # and the callback must take exactly three params.
        if isinstance(val, bool):
            dpg.add_checkbox(label=name, tag=tag, default_value=val,
                             user_data=(model, name), callback=_write_field)
        elif isinstance(val, (int, float)):
            lo, hi = _bounds(fi, float(val))
            dpg.add_slider_float(
                label=name, tag=tag, default_value=float(val),
                min_value=lo, max_value=hi, width=width, clamped=True,
                user_data=(model, name), callback=_write_field,
            )
        elif isinstance(val, str):
            dpg.add_input_text(label=name, tag=tag, default_value=val, width=width,
                               user_data=(model, name), callback=_write_field)


def _write_field(sender, app_data, user_data) -> None:
    model, name = user_data
    try:
        setattr(model, name, app_data)
    except Exception:
        dpg.set_value(sender, getattr(model, name))  # revert on validation error


def refresh_param_editor(model, tag_prefix: str, *, skip: tuple[str, ...] = ("enabled",)) -> None:
    """Push current model values back into the widgets (after a profile load)."""
    for name in type(model).model_fields:
        if name in skip:
            continue
        tag = f"{tag_prefix}__{name}"
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, getattr(model, name))
