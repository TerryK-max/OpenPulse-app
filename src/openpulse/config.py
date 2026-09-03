"""Configuration models. Phase A: just the device config and app settings.

The effect / mixer / profile schema arrives in Phase C-D.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from openpulse.engine.params import EngineConfig
from openpulse.telemetry.derive import DeriveConfig
from openpulse.transport import protocol as P


class DeviceConfig(BaseModel):
    """Runtime config pushed to the box via CTRL_SET_CONFIG (PROTOCOL.md §4.3).

    Ranges mirror link_control.c. A field left at its "keep" sentinel (0 for the
    unit-bearing ones) is not sent. ``ol_lra_period`` / ``od_clamp`` are MEASURED
    hardware constants (HARDWARE.md §3) — leave them at 0 unless you know why.
    """

    sample_rate_hz: int = Field(1000, ge=P.PROTO_RATE_MIN, le=P.PROTO_RATE_MAX)
    failsafe_ms: int = Field(100, ge=P.PROTO_FAILSAFE_MIN, le=P.PROTO_FAILSAFE_MAX)
    amp_max: int = Field(127, ge=1, le=P.PROTO_AMP_MAX)
    loss_policy: int = Field(0, ge=0, le=1)
    underrun_decay_ms: int = Field(20, ge=1, le=255)
    ol_lra_period: int = Field(0, ge=0, le=P.PROTO_OLP_MAX)  # 0 = keep firmware default
    od_clamp: int = Field(0, ge=0, le=255)  # 0 = keep firmware default

    def to_payload(self, *, reset_stats: bool = False) -> bytes:
        flags = P.CFG_FLAG_RESET_STATS if reset_stats else 0
        return P.config_payload(
            sample_rate_hz=self.sample_rate_hz,
            failsafe_ms=self.failsafe_ms,
            amp_max=self.amp_max,
            loss_policy=self.loss_policy,
            underrun_decay_ms=self.underrun_decay_ms,
            ol_lra_period=self.ol_lra_period,
            od_clamp=self.od_clamp,
            flags=flags,
        )

    # -- human-readable derived values for the GUI (HARDWARE.md §2.2 / §3.3) -- #
    @staticmethod
    def olp_to_hz(olp: int) -> float | None:
        return None if olp <= 0 else 1e6 / (olp * 98.46)

    @staticmethod
    def od_clamp_to_volts(odc: int) -> float | None:
        return None if odc <= 0 else 21.59e-3 * odc


class StreamConfig(BaseModel):
    """PC-side streaming parameters (renderer / pacing)."""

    waveform: str = "sim"
    batch: int = Field(12, ge=1, le=P.PROTO_MAX_PAYLOAD)
    lookahead_ms: int = Field(20, ge=4, le=80)
    status_hz: float = Field(10.0, gt=0, le=60)
    dither: bool = False


class TelemetryConfig(BaseModel):
    source: str = "auto"  # auto | ac-shm | ac-udp | replay | synthetic
    udp_host: str = "127.0.0.1"
    udp_port: int = Field(9996, ge=1, le=65535)
    replay_path: str = ""
    scenario: str = "hotlap"
    poll_hz: float = Field(333.0, ge=30, le=1000)
    rpm_max_hint: float = Field(0.0, ge=0, le=25000)  # UDP has no maxRpm; set per car
    derive: DeriveConfig = Field(default_factory=DeriveConfig)


class AppSettings(BaseModel):
    transport: str = "usb"  # "usb" | "mock"
    usb_vid: int = 0x1A86
    usb_pid: int = 0x5730
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
