"""Build a TelemetrySource from settings, with an auto-detect fallback."""

from __future__ import annotations

from openpulse.telemetry.base import SourceStatus, TelemetrySource, TelemetryUnavailable

SOURCE_KINDS = ("auto", "ac-shm", "ac-udp", "replay", "synthetic")


def build_source(
    kind: str,
    *,
    udp_host: str = "127.0.0.1",
    udp_port: int = 9996,
    replay_path: str = "",
    scenario: str = "hotlap",
    rpm_max_hint: float = 0.0,
) -> TelemetrySource:
    kind = kind.lower()
    if kind == "ac-shm":
        from openpulse.telemetry.ac.shared_memory import ACSharedMemorySource

        return ACSharedMemorySource()
    if kind == "ac-udp":
        from openpulse.telemetry.ac.udp import ACUDPSource

        return ACUDPSource(udp_host, udp_port, rpm_max_hint)
    if kind == "replay":
        from openpulse.telemetry.record import ReplaySource

        if not replay_path:
            raise TelemetryUnavailable("replay source needs a replay_path")
        return ReplaySource(replay_path)
    if kind == "synthetic":
        from openpulse.telemetry.synthetic import SyntheticSource

        return SyntheticSource(scenario)
    if kind == "auto":
        return _AutoSource(udp_host, udp_port, rpm_max_hint)
    raise TelemetryUnavailable(f"unknown telemetry source {kind!r}; have {SOURCE_KINDS}")


class _AutoSource(TelemetrySource):
    """Tries AC shared memory first (richest), falls back to AC UDP."""

    name = "auto"

    def __init__(self, udp_host: str, udp_port: int, rpm_max_hint: float):
        self._udp_host = udp_host
        self._udp_port = udp_port
        self._rpm_max_hint = rpm_max_hint
        self._inner: TelemetrySource | None = None
        self._open = False

    def _pick(self) -> TelemetrySource:
        import sys

        if sys.platform == "win32":
            from openpulse.telemetry.ac.shared_memory import ACSharedMemorySource

            shm = ACSharedMemorySource()
            try:
                shm.open()
                if shm.status() != SourceStatus.OFFLINE:
                    return shm
                shm.close()
            except TelemetryUnavailable:
                pass
        from openpulse.telemetry.ac.udp import ACUDPSource

        udp = ACUDPSource(self._udp_host, self._udp_port, self._rpm_max_hint)
        udp.open()
        return udp

    def open(self) -> None:
        self._inner = self._pick()
        self._open = True

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
        self._inner = None
        self._open = False

    def status(self) -> SourceStatus:
        return self._inner.status() if self._inner else SourceStatus.OFFLINE

    def capabilities(self):  # noqa: ANN201
        from openpulse.telemetry.frame import Cap

        return self._inner.capabilities() if self._inner else Cap.NONE

    def session_info(self):  # noqa: ANN201
        return self._inner.session_info() if self._inner else None

    def poll(self, timeout: float):  # noqa: ANN201
        return self._inner.poll(timeout) if self._inner else None

    @property
    def active_name(self) -> str:
        return self._inner.name if self._inner else "auto"
