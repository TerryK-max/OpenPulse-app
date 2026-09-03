# OpenPulse — application PC

Le « mixeur » côté PC pour la box haptique [OpenPulse](../OpenPulse-firmware).
Lit la télémétrie d'un jeu de sim racing, la transforme en un flux d'amplitude
unique et le streame à la box sur le protocole v1 (USB vendor bulk).

> **Dépôt séparé du firmware.** Le firmware n'est ici qu'en référence.
> Plan complet : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## État — Phases A + B

**Phase A — transport** (confirmée sur la box réelle) :

- `openpulse.transport.protocol` — miroir bit-identique de
  `OpenPulse-firmware/src/link/proto.h` + auto-tests.
- `Transport` abstrait ; `UsbVendorTransport` (pyusb + backend `libusb-package`,
  port de `open_box()`) ; `MockTransport` (fausse box en process).
- `BoxSession` — handshake `RESYNC → PING → SET_CONFIG → SET_MODE`, contrôle de
  version (PROTOCOL §7), thread lecteur, RTT.
- `SampleStreamer` — boucle de pacing temps-réel (modèle validé au banc).
- `openpulse-cli` — streamer headless, parité avec `pc_sender/openpulse_send.py`.

**Phase B — télémétrie Assetto Corsa** :

- `TelemetryFrame` normalisée (SI, `Cap` IntFlag) + `TelemetrySource` abstrait.
- `ac-shm` (mémoire partagée `acpmf_*`, Windows) et `ac-udp` (port 9996,
  multi-OS) ; `ac/normalize.py` (unités AC) ; `Deriver` (`lock_ratio`,
  `susp_vel`, jerk/impact, `grip_scrub`, …).
- `SyntheticSource` (scénarios), `ReplaySource` + `TelemetryRecorder`.
- `openpulse-capture` (dump + rapport sanity), `openpulse-bridge` (pont debug :
  streame `grip_scrub` / `lockup` / `rpm`… droit vers la box).
- GUI : fenêtre **Telemetry** (moniteur temps réel + enregistrement + pont).
- Calibration des tags `# VERIFY` à faire contre une vraie capture AC —
  voir [docs/TELEMETRY.md](docs/TELEMETRY.md) et `tests/fixtures/README.md`.

À venir (Phase C) : moteur d'effets, mixeur, profils.
Voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §13.

## Installation

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate      Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
```

pyusb a besoin d'un backend **libusb** : Windows (DLL fournie par le paquet /
Zadig), Debian/Ubuntu `libusb-1.0-0`, macOS `brew install libusb`.

### Windows — bind de l'interface vendor

Le firmware n'a pas encore de descripteurs MS OS 2.0 : lier **une fois**
l'interface 2 (« OpenPulse vendor ») à **WinUSB** ou **libusbK** via
[Zadig](https://zadig.akeo.ie/). Sinon l'app affiche l'erreur « could not claim
the vendor interface ».

## Utilisation

```bash
# sans matériel — fausse box + télémétrie synthétique
openpulse-cli --transport mock --wave sim --duration 10
openpulse-bridge --transport mock --telemetry synthetic --scenario grip_scrub --map grip_scrub
openpulse --transport mock

# avec la box
openpulse-cli --list
openpulse                       # GUI : Connect, puis Start telemetry, puis Bridge to box
openpulse-bridge --map grip_scrub --telemetry auto      # sentir la saturation d'adhérence

# capturer une session AC pour calibrer / tester
openpulse-capture --source ac-shm --seconds 40 --raw --out tests/fixtures/ac_shm_lap.jsonl
```

## Tests

```bash
pytest          # 100 % logiciel, ni jeu ni box
```

## Licence

Apache-2.0.
