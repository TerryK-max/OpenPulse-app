"""Record normalized frames to JSONL and replay them as a TelemetrySource.

Format: one JSON object per line (``dataclasses.asdict`` of a TelemetryFrame,
tuples as lists, ``caps`` as int, NaN allowed). A sidecar ``<path>.meta.json``
holds the SessionInfo + source name. Used by ``tools/telemetry_capture.py`` and
by the test fixtures.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, fields
from pathlib import Path

from openpulse.telemetry.base import SessionInfo, SourceStatus, TelemetrySource
from openpulse.telemetry.frame import Cap, TelemetryFrame

_TUPLE_FIELDS = {
    f.name for f in fields(TelemetryFrame)
    if f.name in {
        "wheel_omega", "wheel_speed_ms", "slip_ratio", "slip_angle", "combined_slip",
        "nd_slip", "load_n", "susp_travel_m", "tyre_press_kpa", "tyre_temp_c",
        "tyre_wear", "tyre_dirt", "tyre_radius_m", "accel_g", "local_vel_ms",
        "susp_vel_ms", "lock_ratio", "damage",
    }
}


_DERIVED_FIELDS = {
    "susp_vel_ms", "lock_ratio", "grip_scrub", "grip_scrub_front", "grip_scrub_rear",
    "jerk_g_s", "impact_g", "gear_change", "rev_limiter", "wheel_speed_ms",
}


def _is_nan(x: object) -> bool:
    return isinstance(x, float) and math.isnan(x)


def frame_to_json(f: TelemetryFrame, *, compact: bool = False) -> dict:
    d = asdict(f)
    d["caps"] = int(f.caps)
    for k in _TUPLE_FIELDS:
        d[k] = list(d[k])
    if not compact:
        return d
    # compact: drop NaN scalars, all-NaN tuples, and derived fields (recomputed
    # on load by the Deriver); round floats. Shrinks fixtures ~5x.
    def r(x):
        if isinstance(x, float):
            return x if (math.isnan(x) or math.isinf(x)) else round(x, 5)
        if isinstance(x, list):
            return [r(y) for y in x]
        return x

    out = {}
    for k, v in d.items():
        if k in _DERIVED_FIELDS or _is_nan(v):
            continue
        if isinstance(v, list) and v and all(_is_nan(x) for x in v):
            continue
        out[k] = r(v)
    return out


def frame_from_json(d: dict) -> TelemetryFrame:
    d = dict(d)
    d["caps"] = Cap(d.get("caps", 0))
    for k in _TUPLE_FIELDS:
        if k in d and d[k] is not None:
            d[k] = tuple(d[k])
    known = {f.name for f in fields(TelemetryFrame)}
    return TelemetryFrame(**{k: v for k, v in d.items() if k in known})


class TelemetryRecorder:
    def __init__(self, path: str | Path, session: SessionInfo | None, source: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._n = 0
        meta = {
            "source": source,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": asdict(session) if session else None,
        }
        self.path.with_suffix(self.path.suffix + ".meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    def write(self, frame: TelemetryFrame) -> None:
        self._fh.write(json.dumps(frame_to_json(frame)) + "\n")
        self._n += 1

    @property
    def count(self) -> int:
        return self._n

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> TelemetryRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def load_frames(path: str | Path) -> list[TelemetryFrame]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(frame_from_json(json.loads(line)))
    return out


class ReplaySource(TelemetrySource):
    """Replays a recorded JSONL file at (scaled) real time. Is a TelemetrySource
    so the whole pipeline can run off it."""

    name = "replay"

    def __init__(self, path: str | Path, *, speed: float = 1.0, loop: bool = True):
        self.path = Path(path)
        self.speed = speed
        self.loop = loop
        self._open = False
        self._frames: list[TelemetryFrame] = []
        self._i = 0
        self._t0 = 0.0
        self._base = 0.0
        self._session: SessionInfo | None = None

    def open(self) -> None:
        self._frames = load_frames(self.path)
        if not self._frames:
            from openpulse.telemetry.base import TelemetryUnavailable

            raise TelemetryUnavailable(f"no frames in {self.path}")
        meta_path = self.path.with_suffix(self.path.suffix + ".meta.json")
        if meta_path.exists():
            import json as _j

            m = _j.loads(meta_path.read_text(encoding="utf-8"))
            if m.get("session"):
                self._session = SessionInfo(**m["session"])
        self._i = 0
        self._t0 = time.perf_counter()
        self._base = self._frames[0].t_mono
        self._open = True

    def close(self) -> None:
        self._open = False

    def status(self) -> SourceStatus:
        if not self._open:
            return SourceStatus.OFFLINE
        return SourceStatus.LIVE

    def capabilities(self) -> Cap:
        return self._frames[0].caps if self._frames else Cap.NONE

    def session_info(self) -> SessionInfo | None:
        return self._session

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._open or not self._frames:
            return None
        deadline = time.perf_counter() + max(0.0, timeout)
        while True:
            if self._i >= len(self._frames):
                if not self.loop:
                    return None
                self._i = 0
                self._t0 = time.perf_counter()
                self._base = self._frames[0].t_mono
            fr = self._frames[self._i]
            due = self._t0 + (fr.t_mono - self._base) / max(self.speed, 1e-6)
            now = time.perf_counter()
            if now >= due:
                self._i += 1
                return replace_t_mono(fr, now)
            if now >= deadline:
                return None
            time.sleep(min(0.002, due - now))


def replace_t_mono(f: TelemetryFrame, t: float) -> TelemetryFrame:
    from dataclasses import replace

    return replace(f, t_mono=t)
