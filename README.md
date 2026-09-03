# OpenPulse — application PC

Le « mixeur » côté PC pour la box haptique [OpenPulse](../OpenPulse-firmware).
Lit la télémétrie d'un jeu de sim racing, la transforme en un flux d'amplitude
unique et le streame à la box sur le protocole v1 (USB vendor bulk).

> **Dépôt séparé du firmware.** Le firmware n'est ici qu'en référence.
> Plan complet : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## État — Phase A (squelette + transport)

Fait :

- `openpulse.transport.protocol` — miroir bit-identique de
  `OpenPulse-firmware/src/link/proto.h` (framing, CRC-8, structs) + auto-tests.
- `Transport` abstrait ; `UsbVendorTransport` (pyusb, port de `open_box()`) ;
  `MockTransport` (fausse box en process, pour tourner sans matériel).
- `BoxSession` — handshake `RESYNC → PING → SET_CONFIG → SET_MODE`, contrôle de
  version (PROTOCOL §7), thread lecteur (STATUS / LOG / FAULT / PONG), RTT.
- `SampleStreamer` — boucle de pacing temps-réel (modèle validé au banc).
- `openpulse-cli` — streamer headless, parité avec
  `OpenPulse-firmware/tools/pc_sender/openpulse_send.py`.
- GUI dearpygui minimale : panneaux **Connexion** + **Diagnostics**.

À venir : télémétrie Assetto Corsa (mémoire partagée + UDP), moteur d'effets,
mixeur, profils. Voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §13.

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
# sans matériel — fausse box
openpulse-cli --transport mock --wave sim --duration 10
openpulse --transport mock

# avec la box
openpulse-cli --list
openpulse-cli --wave sim
openpulse
```

## Tests

```bash
pytest          # 100 % logiciel, ni jeu ni box
```

## Licence

Apache-2.0.
