"""Phase-A test waveforms — placeholder signal sources until the effect engine
(Phase C) exists. Ported from ``tools/pc_sender/openpulse_send.py``.

Each generator maps a sample index to an int8 amplitude in 0..127.
"""

from __future__ import annotations

import math
from collections.abc import Callable

Waveform = Callable[[int, int], int]


def wave_sim(i: int, rate: int) -> int:
    """Mirrors the firmware haptic_pattern_simracing feel: modulated rumble with
    periodic kerb impacts."""
    ms = i * 1000 // rate
    lvl = 0x48
    wob = (ms // 250) % 4
    lvl += -10 if wob == 3 else wob * 7
    lvl += ((ms // 10) * 7) % 11 - 5
    if ms % 1500 < 30:
        lvl = 0x7F
    return max(0, min(0x7F, lvl))


def wave_sine(i: int, rate: int) -> int:
    return int(63 + 63 * math.sin(2 * math.pi * 1.0 * i / rate))  # 1 Hz envelope


def wave_ramp(i: int, rate: int) -> int:
    return int((i % rate) * 127 / rate)  # 1 Hz sawtooth


def wave_off(i: int, rate: int) -> int:
    return 0


WAVEFORMS: dict[str, Waveform] = {
    "sim": wave_sim,
    "sine": wave_sine,
    "ramp": wave_ramp,
    "off": wave_off,
}
