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
- `openpulse-capture` / `openpulse-analyze` / `openpulse-trim` (capture, stats,
  fixture) ; `openpulse-bridge` (pont debug : `grip_scrub` / `lockup` / `rpm`…
  droit vers la box).
- GUI : fenêtre **Telemetry** (moniteur temps réel + enregistrement + pont).
- Unités/axes AC **calibrés** sur une vraie capture mémoire partagée ; la même
  passe côté UDP reste à faire — voir [docs/TELEMETRY.md](docs/TELEMETRY.md).

**Phase C — moteur d'effets** :

- `Effect` ABC + `engine/dsp.py` + **12 effets** (`road`, `engine`, `grip_scrub`,
  `lockup`, `wheelspin`, `abs_pulse`, `tc_cut`, `kerb`, `gearshift`,
  `rev_limiter`, `impact`, `offtrack`) — voir [docs/EFFECTS.md](docs/EFFECTS.md).
- `mixer` (ducking, soft-limiter, noise gate, garde thermique) + `pipeline` →
  un flux 0..1 ; `PipelineStreamer` vers la box.
- `openpulse-preview` : rejoue une capture dans le moteur → WAV + rapport.
- GUI : fenêtres **Effects** (params live) + **Mixer** (VU + scope).
- Calibré sur le tour réel ; tuning au ressenti sur la box à venir.

À venir (Phase D) : profils par voiture, `SET_CONFIG` GUI, packaging.
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

# conduire la box depuis le moteur d'effets (avec la box + AC)
openpulse                       # GUI : Connect -> Start telemetry -> "Effect engine"

# capturer / analyser / prévisualiser / trimer une session AC
openpulse-capture --source ac-shm --seconds 40 --out recordings/lap.jsonl
openpulse-analyze recordings/lap.jsonl --on-track-only     # percentiles -> calibration
openpulse-preview recordings/lap.jsonl                     # -> WAV du rendu moteur + rapport
openpulse-trim recordings/lap.jsonl tests/fixtures/ac_shm_<voiture>.jsonl --every 40
```

## Tests

```bash
pytest          # 100 % logiciel, ni jeu ni box
```

## Licence

Apache-2.0.
