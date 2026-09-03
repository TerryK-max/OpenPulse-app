"""GUI entrypoint (`openpulse`)."""

from __future__ import annotations

import argparse

from openpulse.config import AppSettings
from openpulse.gui.app import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenPulse — sim-racing haptic bridge (GUI)")
    ap.add_argument("--transport", choices=("usb", "mock"), default="usb",
                    help="start with this transport selected")
    a = ap.parse_args(argv)
    settings = AppSettings(transport=a.transport)
    run(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
