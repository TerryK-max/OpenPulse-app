# OpenPulse — PC application

The PC-side "mixer" for the [OpenPulse](../OpenPulse-firmware) haptic box.
Reads telemetry from a sim-racing game, turns it into a single amplitude
stream, and streams it to the box over protocol v1 (USB vendor bulk).

> **Separate repo from the firmware.** The firmware is here only as reference.
> Full plan: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Status — Phases A + B + C

**Phase A — transport** (confirmed on the real box):

- `openpulse.transport.protocol` — bit-identical mirror of
  `OpenPulse-firmware/src/link/proto.h` + self-tests.
- `Transport` abstraction; `UsbVendorTransport` (pyusb + `libusb-package`
  backend, port of `open_box()`); `MockTransport` (in-process fake box).
- `BoxSession` — `RESYNC → PING → SET_CONFIG → SET_MODE` handshake, version
  check (PROTOCOL §7), reader thread, RTT.
- `SampleStreamer` — real-time pacing loop (bench-validated model).
- `openpulse-cli` — headless streamer, parity with `pc_sender/openpulse_send.py`.

**Phase B — Assetto Corsa telemetry**:

- Normalized `TelemetryFrame` (SI units, `Cap` IntFlag) + `TelemetrySource`
  abstraction.
- `ac-shm` (shared memory `acpmf_*`, Windows) and `ac-udp` (port 9996,
  multi-OS); `ac/normalize.py` (AC units); `Deriver` (`lock_ratio`,
  `susp_vel`, jerk/impact, `grip_scrub`, …).
- `SyntheticSource` (scenarios), `ReplaySource` + `TelemetryRecorder`.
- `openpulse-capture` / `openpulse-analyze` / `openpulse-trim` (capture, stats,
  fixture); `openpulse-bridge` (debug bridge: `grip_scrub` / `lockup` / `rpm`…
  straight to the box).
- GUI: **Telemetry** window (live monitor + recording + bridge).
- AC units/axes **calibrated** against a real shared-memory capture; the same
  pass on the UDP side is still open — see [docs/TELEMETRY.md](docs/TELEMETRY.md).

**Phase C — effect engine**:

- `Effect` ABC + `engine/dsp.py` + **12 effects** (`road`, `engine`,
  `grip_scrub`, `lockup`, `wheelspin`, `abs_pulse`, `tc_cut`, `kerb`,
  `gearshift`, `rev_limiter`, `impact`, `offtrack`) — see
  [docs/EFFECTS.md](docs/EFFECTS.md).
- `mixer` (ducking, soft limiter, noise gate, thermal guard) + `pipeline` →
  a 0..1 stream; `PipelineStreamer` to the box.
- `openpulse-preview`: replays a capture through the engine → WAV + report.
- GUI: **Effects** (live params) + **Mixer** (VU + scope) windows.
- Calibrated against the real lap; feel-tuning on the box still to come.

Coming up (Phase D): per-car profiles, `SET_CONFIG` GUI, packaging.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §13.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

pyusb needs a **libusb** backend: Windows (DLL bundled with the package /
Zadig), Debian/Ubuntu `libusb-1.0-0`, macOS `brew install libusb`.

### Windows — binding the vendor interface

The firmware has no MS OS 2.0 descriptors yet: bind interface 2 ("OpenPulse
vendor") to **WinUSB** or **libusbK** via [Zadig](https://zadig.akeo.ie/)
**once**. Otherwise the app shows "could not claim the vendor interface".

## Usage

```bash
# no hardware — fake box + synthetic telemetry
openpulse-cli --transport mock --wave sim --duration 10
openpulse-bridge --transport mock --telemetry synthetic --scenario grip_scrub --map grip_scrub
openpulse --transport mock

# with the box
openpulse-cli --list
openpulse                       # GUI: Connect, then Start telemetry, then Bridge to box
openpulse-bridge --map grip_scrub --telemetry auto      # feel the grip saturation

# drive the box from the effect engine (with the box + AC)
openpulse                       # GUI: Connect -> Start telemetry -> "Effect engine"

# capture / analyze / preview / trim an AC session
openpulse-capture --source ac-shm --seconds 40 --out recordings/lap.jsonl
openpulse-analyze recordings/lap.jsonl --on-track-only     # percentiles -> calibration
openpulse-preview recordings/lap.jsonl                     # -> WAV of the engine render + report
openpulse-trim recordings/lap.jsonl tests/fixtures/ac_shm_<car>.jsonl --every 40
```

## Tests

```bash
pytest          # 100% software, no game or box needed
```

## Licence

Apache-2.0.
