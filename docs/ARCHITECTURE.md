# OpenPulse — application PC (le « mixeur »)

> **Statut : Phase A implémentée** (squelette + transport + GUI minimale — voir
> §13). Les phases B+ restent à faire. Ce document est le plan cible ; il s'appuie
> entièrement sur les docs du firmware (dépôt séparé, fourni en référence) :
> `OpenPulse-firmware/docs/` — `PROTOCOL.md`, `ARCHITECTURE.md`, `HARDWARE.md`,
> `ROADMAP.md`.
>
> **Décisions actées** : « DMA » = interface **mémoire partagée officielle d'AC**
> (`acpmf_*`). Dépôt **séparé** du firmware. **Multi-OS** (Windows + Linux +
> macOS) — cas d'usage explicite : box sur un PC, jeu + télémétrie UDP sur un
> autre. Dépendances tierces **autorisées** (numpy, pydantic, dearpygui, pyusb,
> libusb-package…).

---

## 0. En une phrase

Une application Python (GUI **dearpygui**) qui lit la **télémétrie d'un jeu**,
la transforme en un **flux d'amplitude unique** via un moteur d'effets modulaire,
et le **streame à la box OpenPulse** sur le protocole v1 (USB vendor bulk
aujourd'hui, 2.4 GHz plus tard). Premier jeu : **Assetto Corsa** (mémoire
partagée *et* UDP). L'architecture est pensée pour ajouter d'autres jeux sans
toucher au moteur d'effets ni au transport.

---

## 1. Ce que le firmware impose (contraintes, non négociables)

Tiré des docs firmware. L'app doit s'y plier au bit près.

| Contrainte | Détail | Conséquence pour l'app |
|---|---|---|
| **La box est un *renderer* bête** | Aucune synthèse d'effet à bord (juste self-test + rampe de sécurité). | **Tout** le calcul d'effet, le mixage, la mise en forme, la protection thermique se font côté PC. |
| **Entrée = enveloppe d'amplitude** | La box commute la LRA en boucle ouverte à **~158,7 Hz** fixe. L'échantillon `int8` n'est *pas* de l'audio : c'est l'**amplitude** de cette porteuse. | Nos « fréquences » d'effets sont des fréquences de **modulation d'amplitude**, bande utile **~1–80 Hz** (au-delà, l'actionneur ne suit pas : montée mécanique 10–20 ms). |
| **Échantillon** | `int8` signé, `-127..+127`. `0` = pas d'entraînement. Le PC envoie normalement `0..127` ; le négatif = phase inverse (freinage anti-phase explicite). | v1 : sortie `0..127`. Le bipolaire (edge d'impact net) est une option ultérieure. |
| **Débit** | `sample_rate_hz` réglable `250..4000`, défaut **1000** (2000 OK). Banc : la box tient **≥ 4 kHz sans perte**. | Moteur DSP cadencé à `sample_rate_hz` du device (défaut 1000). Phase 5 firmware (PWM) inutile. |
| **Trame** | `[TYPE][SEQ][LEN][PAYLOAD≤56][CRC8]`, total 4..60 o. CRC-8/SMBUS (poly `0x07`). LE. **Une trame = une transaction USB bulk.** | `batch ≤ 56` échantillons/trame. `proto.py` mirroir bit-identique de `src/link/proto.h`. |
| **Handshake** | `RESYNC` → `PING` → `SET_CONFIG(version=1,…)` → `SET_MODE(SAMPLES)` → amorçage FIFO → stream. `STATUS_REQ` à ~10 Hz. | **`RESYNC` en premier** (fix de banc 2026-09-02 : reseat le SEQ, fait céder le self-test local de la box). |
| **Version** | `SET_CONFIG.version != 1` → `FAULT_VERSION` + plan data bloqué. `PONG.proto_version` doit valoir 1. | À la connexion : `PING`, vérifier `proto_version` **et** `fw_major.fw_minor` ; mismatch → refus de streamer + message clair (PROTOCOL §7). |
| **Failsafe** | Pas de trame data valide pendant `failsafe_ms` (défaut 100) → la box rampe à 0. Reset USB / déconnexion → rampe immédiate. | Quand la télémétrie stoppe (pause, menu, jeu fermé) : ramper notre sortie à 0 et envoyer des zéros (évite de spammer `failsafe_trips`). |
| **Lookahead** | Le PC garde la FIFO box remplie ~40 ms d'avance (réglable). FIFO box = 256 échantillons. | Buffer d'anticipation réglable **8–60 ms**, défaut ~20 ms. Compromis latence/jitter exposé dans la GUI. |
| **Perte** | Lossy-but-live. `loss_policy` 0 = zero-fill (défaut), 1 = hold-last. Pas d'ARQ. | En USB la perte est quasi nulle ; garder zero-fill. Le choix comptera pour la RF (Phase 4). |
| **OD_CLAMP / OL_LRA_PERIOD** | Constantes **[MESURÉES]** (`0xC0` ≈ 4,15 V crête ; `64` ≈ 158,7 Hz). Ne pas changer sans banc. | La GUI expose `od_clamp` derrière un volet « Avancé (matériel) » avec l'avertissement HARDWARE.md ; `ol_lra_period` en lecture quasi seule. Le réglage de « volume » se fait en **soft** (mise à l'échelle de l'amplitude), pas via `OD_CLAMP`. |
| **Thermique** | Actionneur salvagé (Taptic Engine). Démag. NdFeB irréversible dès ~80 °C. Niveau *soutenu* `0x48/127` ≈ 2,35 V jugé « thermiquement tenable » au banc. | **Garde thermique logicielle obligatoire** : intégrateur d'énergie + repli de gain + prise en compte de `drv_status` OVER_TEMP / `FAULT_DRV_OT`. |
| **Windows** | Pas de descripteurs MS OS 2.0 (firmware Phase 3.6 non faite) → l'interface 2 (vendor) nécessite un bind **WinUSB/libusbK via Zadig**, une fois. | L'app détecte « device vu mais interface 2 non réclamable » → écran d'aide guidé. Disparaît quand 3.6 atterrit. |

**Réutilisation directe** : `OpenPulse-firmware/tools/pc_sender/` (`proto.py`,
`openpulse_send.py`) est un émetteur minimal **déjà validé au banc**. La couche
transport de l'app en est le portage propre ; la boucle de pacing est reprise
telle quelle.

---

## 2. Principes directeurs

1. **Frontière nette télémétrie ↔ effets ↔ transport.** Chaque couche a une
   interface abstraite ; on ajoute un jeu = une classe `TelemetrySource` + un
   normaliseur, rien d'autre ne bouge.
2. **Schéma de télémétrie normalisé, unités SI, agnostique du jeu.** Les effets
   ne voient jamais une structure Assetto Corsa.
3. **Le moteur d'effets est un banc de petites unités DSP indépendantes.** Chacune
   testable seule, activable/désactivable, réglable à chaud.
4. **Un seul actionneur → un « langage vibratoire ».** Chaque effet a une
   signature (bande de modulation + texture) distincte pour rester
   reconnaissable une fois tout mélangé (§5.1).
5. **GUI et moteur séparés.** dearpygui tourne dans son thread ; le moteur dans
   le sien. Échange par snapshots + files de commandes. La GUI ne peut jamais
   faire caler le pipeline.
6. **Tout est testable sans matériel.** `MockTransport` + rejeu de télémétrie
   enregistrée → CI 100 % Python, sans jeu, sans box.
7. **`proto.py` suit `proto.h` au bit près.** Auto-tests (tailles de structs,
   vecteur CRC) qui échouent bruyamment en cas de dérive.

**Non-objectifs v1** : multi-actionneur, retour de force volant / audio, jeux
autres qu'Assetto Corsa, mode `ENVELOPE`, persistance DataFlash, calibration
thermique NTC, descripteurs MS OS 2.0 (côté firmware).

---

## 3. Vue d'ensemble du pipeline

```
┌─ Thread T1 — Acquisition télémétrie ───────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ AC mém.part. │  │  AC UDP:9996 │  │  (futur jeu) │   sources concrètes   │
│  │  acpmf_*     │  │  RTCarInfo   │  │              │                       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                       │
│         └───────────┬─────┴─────────────────┘                               │
│                     ▼ structs brutes                                       │
│            ┌──────────────────┐                                            │
│            │ normalize (AC)   │──► TelemetryFrame  (SI, générique,         │
│            │ + derive (Δ, jerk)│      + CapabilitySet)                      │
│            └────────┬─────────┘                                            │
└─────────────────────┼──────────────────────────────────────────────────────┘
                      ▼  swap atomique « dernière frame »          + Recorder
┌─ Thread T2 — DSP + sortie  (cadence perf_counter) ─────────────────────────┐
│   ┌──────────────────────────────────────────────────────┐                 │
│   │ Pipeline.generate(n, fs)                              │                 │
│   │  road  engine  kerb  lockup  wheelspin  abs  shift …  │  chaque effet → │
│   │   └──────┴──────┴──┬───┴──────────┴──────┴─────┘       │  vecteur 0..1   │
│   │                    ▼                                   │  @ fs, n éch.   │
│   │        ┌────────────────────────┐                      │                 │
│   │        │ Mixer + Master         │  lit vs événements,  │                 │
│   │        │  ducking / priorité    │  limiteur soft,      │                 │
│   │        │  noise gate            │  garde thermique     │                 │
│   │        └───────────┬────────────┘                      │                 │
│   └────────────────────┼───────────────────────────────────┘                │
│                        ▼  float32 0..1 @ fs                                 │
│              ┌───────────────────┐                                         │
│              │ Renderer          │  quantize int8 · batch · buffer         │
│              │                   │  lookahead · pacing temps-réel          │
│              └────────┬──────────┘                                         │
│                       ▼  trames DATA_SAMPLES                                │
│              ┌───────────────────┐                                         │
│              │ Transport (USB)   │  proto v1 · EP2 OUT · handshake         │
│              └────────┬──────────┘                                         │
└───────────────────────┼────────────────────────────────────────────────────┘
                        ▼
                 ╔═════════════╗
                 ║  OpenPulse  ║   firmware renderer (bête)
                 ║     box     ║
                 ╚═════════════╝
                        ▲  PONG / STATUS_REP / LOG / FAULT
┌─ Thread T3 — retour box ───────────────────────────────────────────────────┐
│   parse → AppState.device (stats, version, faults, RTT)                     │
└────────────────────────────────────────────────────────────────────────────┘

   AppState  (snapshots 60 Hz + files de commandes)  ◄──►  Thread GUI (dearpygui)
```

---

## 4. Les couches, une par une

### 4.1 Acquisition télémétrie — `openpulse/telemetry/`

**Interface abstraite** (`telemetry/base.py`) :

```python
class TelemetrySource(Protocol):
    name: str
    def open(self) -> None: ...
    def close(self) -> None: ...
    def state(self) -> SourceState           # DISCONNECTED / WAITING_GAME / MENU / ON_TRACK
    def capabilities(self) -> CapabilitySet  # quels champs la source sait remplir
    def poll(self, timeout_s: float) -> TelemetryFrame | None
    def session_info(self) -> SessionInfo | None   # jeu, voiture, circuit, max_rpm…
```

`registry.py` : découverte + auto-sélection (essaie mém. partagée, retombe sur
UDP), ou choix manuel dans la GUI.

#### Assetto Corsa — mémoire partagée (ce que tu appelles « DMA »)

> ⚠️ **Hypothèse à confirmer** : « DMA » = l'interface **mémoire partagée
> officielle d'AC** (fichiers *memory-mapped* nommés). Si tu voulais dire lecture
> de process par pointeur / carte DMA matérielle, la couche d'acquisition change
> — voir §14 Q1.

- 3 objets Windows : `Local\acpmf_physics`, `Local\acpmf_graphics`,
  `Local\acpmf_static`, ouverts en `mmap.mmap(-1, size, tagname)`.
- Layout décrit par `ctypes.Structure` dans `telemetry/ac/structs.py`, transcrit
  **avec citation de la source** (SDK Python officiel d'AC / `sdk/dev/`), comme
  le firmware transcrit les datasheets. Tolérance : champs ajoutés en *queue* de
  page selon la version du jeu → on lit `min(len(buf), sizeof(struct))`.
- Cadence : poll ~**333–500 Hz**, on ne consomme que si `physics.packetId` a
  changé (détecte aussi les frames sautées).
- Champs clés utilisés : `speedKmh`, `rpms`, `gear`, `gas`, `brake`, `clutch`,
  `steerAngle`, `accG[3]`, `wheelSlip[4]`, `wheelLoad[4]`, `wheelAngularSpeed[4]`,
  `suspensionTravel[4]`, `wheelsPressure[4]`, `tyreCoreTemperature[4]`,
  `slipRatio[4]`/`slipAngle[4]` (builds récents), `numberOfTyresOut`,
  `pitLimiterOn`, `carDamage[5]`, `drs`, `tc`, `abs`, `localAngularVel[3]`.
- `graphics.status` (OFF/REPLAY/LIVE/PAUSE), `isInPit`, `surfaceGrip`,
  `completedLaps`.
- `static.maxRpm`, `static.carModel`, `static.track` → `SessionInfo` + choix de
  profil automatique.
- **Source préférée** : plus riche (course de suspension, temp. pneus, dégâts,
  roues hors-piste, `packetId`).

#### Assetto Corsa — UDP

- La box de télémétrie UDP d'AC écoute sur **port 9996** (localhost ou PC
  distant).
- Poignée de main : envoyer `handshake{identifier, version, operationId}` :
  `0` = HANDSHAKE → réponse `HandshakerResponse` (nom voiture/pilote, circuit) ;
  `1` = SUBSCRIBE_UPDATE → flux `RTCarInfo` ; `3` = DISMISS à la fermeture.
- `RTCarInfo` (~328 o) : `speed_Kmh`, `engineRPM`, `gear`, `gas`/`brake`/`clutch`,
  `steer`, `accG[3]`, `isAbsEnabled`, **`isAbsInAction`**, **`isTcInAction`**,
  `isInPit`, `isEngineLimiterOn`, `wheelAngularSpeed[4]`, `slipAngle[4]`,
  `slipRatio[4]`, `tyreSlip[4]`, `load[4]`, `tyreDirtyLevel[4]`,
  `suspensionHeight[4]`, `carCoordinates[3]`, `carPositionNormalized`, `carSlope`.
- **Manque vs mém. partagée** : `maxRpm` (→ défaut par voiture ou saisi),
  `suspensionTravel` (on a `suspensionHeight` → dérivée), `tyreCoreTemperature`,
  `carDamage`, `numberOfTyresOut` (approché par `tyreDirtyLevel`).
- **Gagne** : `isAbsInAction` / `isTcInAction` en direct (en mém. partagée on les
  dérive).
- Réception : socket non bloquante + boucle recv ; ~90–100 Hz effectifs.
- Cas d'usage : PC de jeu ≠ PC de la box, ou mém. partagée indisponible.

`CapabilitySet` porte, par source, les champs valides. Un effet dont une entrée
manque : bascule sur un estimateur de repli, sinon se désactive avec une note
GUI (« kerb : nécessite la course de suspension → source mém. partagée »).

### 4.2 Normalisation — `telemetry/frame.py`, `telemetry/derive.py`

`TelemetryFrame` (dataclass gelée, ~immuable) — SI, repère roues `[FL, FR, RL, RR]` :

| Groupe | Champs |
|---|---|
| méta | `t_mono` (s), `packet_id`, `dt` (s), `source`, `caps` |
| session | `on_track: bool`, `paused`, `in_pit`, `surface_grip` |
| véhicule | `speed_ms`, `rpm`, `rpm_max`, `gear` (int, −1=R 0=N), `throttle` 0..1, `brake` 0..1, `clutch` 0..1, `steer_rad` |
| roues ×4 | `slip_ratio`, `slip_angle_rad`, `load_n`, `susp_travel_m`, `susp_vel_ms` *(dérivé)*, `wheel_speed_ms`, `tyre_press_kpa`, `tyre_temp_c`, `lock_ratio` *(dérivé)* |
| châssis | `accel_g` (x, y, z), `yaw_rate`, `jerk_g_s` *(dérivé)*, `ride_height_m` |
| flags | `abs_active`, `tc_active`, `abs_installed`, `tc_installed`, `drs`, `rev_limiter`, `engine_limiter_flag`, `off_track_wheels` (0..4) |
| événements | `gear_changed` *(edge)*, `impact_g` *(dérivé, pic)*, `damage_delta` *(dérivé)* |

`derive.py` — état inter-frames (garde une petite fenêtre glissante) :
- vitesse de suspension = différence finie filtrée de `susp_travel_m` (ou de
  `susp_height` en UDP).
- `lock_ratio` = `wheel_speed / max(speed_ms, ε)` par roue → détection blocage
  (< seuil au freinage) / patinage (> seuil à l'accélération).
- `jerk_g_s` = dérivée de `‖accel_g‖` → détection d'impact.
- `rev_limiter` = `rpm ≥ rpm_max − ε` **ou** `engine_limiter_flag`.
- `gear_changed` = front sur `gear`.
- lissage / anti-aliasing des grandeurs bruitées (one-pole).

Le normaliseur AC (`telemetry/ac/normalize.py`) est **le seul** endroit qui
connaît les unités et le repère d'AC (km/h→m/s, ordre des roues, signe du
steer…).

### 4.3 Moteur d'effets — `openpulse/engine/`

**Interface** (`engine/effect.py`) :

```python
@dataclass
class EffectContext:
    fs: int                     # = sample_rate_hz du device
    t0: float                   # temps du 1er échantillon du bloc
    n: int                      # taille du bloc
    caps: CapabilitySet
    rng: np.random.Generator

class Effect(Protocol):
    id: str
    kind: Literal["bed", "event"]   # lit continu | événementiel
    priority: int                    # arbitrage / ducking
    def configure(self, params: EffectParams) -> None: ...
    def reset(self) -> None: ...
    def process(self, frame: TelemetryFrame, ctx: EffectContext) -> EffectBlock
```

`EffectBlock` = `np.ndarray float32 [n]` dans `0..1` (contribution d'amplitude)
+ un `duck: float` optionnel (dB d'atténuation demandée sur le lit pendant que
l'effet est actif). Chaque effet **garde sa continuité de phase** entre blocs
(accumulateur de phase d'oscillateur, machine à états d'enveloppe) et **lisse ses
paramètres** issus de la télémétrie (slew one-pole) pour éviter le *zipper
noise*.

**Primitives DSP** (`engine/dsp.py`, numpy, sans état global) :
`band_noise()` (bruit filtré pour textures), `Oscillator` (sin/tri/carré/pulse à
rapport cyclique, phase-continu), `OneShotEnv(attack, hold, decay)` déclenchable,
`OnePole` (lissage / slew), `EdgeDetector` / `Schmitt`, `SoftLimiter` (genou
tanh), `NoiseGate` (hystérésis), `LeakyEnergy` (modèle thermique).

**Catalogue v1 (Assetto Corsa)** — chaque ligne = un module dans `engine/effects/` :

| Effet | Type | Entrées | Détection / mapping | Signature (bande, texture) | Paramètres GUI |
|---|---|---|---|---|---|
| `road` | lit | `speed_ms`, contenu HF de `susp_travel` / variance `load_n` | RMS HF suspension × courbe(vitesse) | bruit large-bande lissé **5–40 Hz**, niveau plancher | gain, courbe vitesse, plancher/plafond |
| `engine_rpm` | lit | `rpm`, `rpm_max`, `throttle` | sous-harmonique du régime, ramené dans **8–30 Hz**, monte avec la charge | pulsation régulière lente | gain, diviseur, mélange charge |
| `rev_limiter` | évén. | `rev_limiter` | flag / seuil régime | **stutter dur ~7–10 Hz**, fort | seuil, gain, fréquence |
| `gearshift` | évén. | `gear_changed`, `clutch` | front sur la rapport | **1 thump 35–60 ms**, attaque raide (montée ≠ descente) | gain, durée, up/down |
| `kerb` | évén. | oscillation `susp_travel[essieu]`, pics `load_n`, `off_track_wheels` | oscillation corrélée sur un essieu + sortie partielle | **burst ~45–60 Hz** modulé, intensité = amplitude d'oscillation | gain, seuil, fréquence |
| `lockup` | évén. | `lock_ratio[avant]`, `brake` | `lock_ratio < seuil` **et** `brake > seuil` | **grind lent ~12–18 Hz**, rugueux | seuil slip, gain, fréquence |
| `wheelspin` | évén. | `lock_ratio[arrière]`, `throttle`, `slip_ratio` | survitesse roue arrière à l'accélération | **buzz rapide ~25–35 Hz** | seuil, gain, fréquence |
| `abs_pulse` | évén. | `abs_active` (UDP) / dérivé (mém.part.), `brake` | flag ABS actif | **pulsation régulière ~15 Hz**, medium | gain, fréquence, source |
| `tc_cut` | évén. | `tc_active` / slip dérivé | flag TC actif | **saccade ~20 Hz**, faible | gain, fréquence |
| `impact` | évén. | `jerk_g_s`, `impact_g`, `damage_delta` | `jerk` > seuil ou nouveau dégât | **gros transitoire 40–120 ms**, attaque max, intensité = `|jerk|` | seuil, gain, courbe, cooldown |
| `offtrack` | évén. | `off_track_wheels` / `tyre_dirty`, `speed_ms` | nb roues dehors × vitesse | **rumble grossier bruité ~8–20 Hz** | gain, mix vitesse, par roue |
| `slip_slide` *(option)* | lit | `slip_angle` arrière, `yaw_rate` | l'arrière décroche progressivement | **tremor montant 10–20 Hz** | gain, seuil, on/off |

Ajouter un effet = déposer un fichier dans `engine/effects/`, l'enregistrer dans
le registre, ajouter son bloc de params au schéma de profil. Zéro impact
ailleurs.

### 4.4 Mixeur & master — `engine/mixer.py`

Ordre de traitement d'un bloc :

1. **Lit** : somme des `bed`, mise à l'échelle pour que le lit combiné ≤
   `bed_ceiling` (défaut ~0,5).
2. **Événements** : somme des `event`. Chacun peut demander un **ducking** du lit
   (atténuation X dB pendant sa durée, release court).
3. **Arbitrage par priorité** : si deux événements haute priorité se recouvrent
   (`impact` + `kerb`), le moins prioritaire est **atténué, pas supprimé** — on
   sent encore le kerb sous l'impact. Ordre : `impact` > `kerb` >
   `lockup`/`wheelspin` > `abs`/`tc` > `gearshift` > `rev_limiter` > `engine` >
   `road`.
4. **Master gain**.
5. **Limiteur soft** (genou tanh) à 1,0 — la somme d'effets coïncidents ne clippe
   jamais durement.
6. **Noise gate** : sous `gate_floor` (≈ 0,02) pendant `gate_hold` → sortie
   **0 dur** (protège l'actionneur, évite le bourdonnement d'inactivité).
   Hystérésis pour ne pas hacher.
7. **Garde thermique** (`engine/thermal.py`) :
   - `E ← E·decay + Σ sample²·dt` (intégrateur à fuite, constante de temps
     ~dizaines de s à quelques min — cf. HARDWARE §4, actionneur boulonné à un
     support = dissipateur).
   - `E > soft_threshold` → `master *= foldback(E)` (courbe douce).
   - `drv_status` bit OVER_TEMP **ou** `FAULT_DRV_OT` reçu → sortie forcée à 0 +
     alarme GUI jusqu'à acquittement.
   - Constantes = estimations conservatrices (niveau soutenu cible autour de
     `0x48/127`), réglables ; calibrables plus tard contre la NTC (firmware
     Phase 6.3).

Sortie : `float32 [n]` dans `0..1`.

### 4.5 Renderer / sortie device — `openpulse/output/renderer.py`

- **fs interne = `sample_rate_hz` du device** (défaut 1000, ≤ 2000). Pas de
  rééchantillonnage en v1. (Si un jour on veut un fs interne fixe indépendant du
  device : resampler polyphase, isolé ici.)
- Le pipeline produit des **blocs** de ~8–16 échantillons (2–16 ms).
- **Quantification** : `int8(clip(round(x·127), 0, 127))`. Dither TPDF ~0,5 LSB
  optionnel (lisse les rampes lentes) — toggle GUI.
- **Batch + trames** : `batch` échantillons par `DATA_SAMPLES` (défaut 8, ≤ 56).
- **Buffer lookahead** : maintenir `lookahead_ms` d'avance (défaut ~20 ms,
  8–60). Amorçage avant le stream, exactement comme `openpulse_send.py`.
- **Pacing** (repris tel quel du sender de référence) :
  `target = int((now − t0)·fs) + lookahead ; while i < target: send(batch)`.
- **`STATUS_REQ`** à 10 Hz (émis ici ou par T3).
- **Télémétrie périmée** (pas de frame depuis > `stale_ms`, ou `on_track` faux,
  ou `paused`) → enveloppe cible 0, on continue d'envoyer des zéros (pas de
  `failsafe_trips` inutiles). Reprise auto à la frame suivante.
- **`amp_max` box laissé à 127** : toute la mise en forme se fait en soft (plus
  de résolution, plus doux). `od_clamp` reste le plafond analogique.

### 4.6 Transport — `openpulse/transport/`

```python
class Transport(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def send(self, frame: bytes) -> None
    def frames_in(self) -> Iterator[bytes]      # trames reçues, déjà déframées
    def stats(self) -> TransportStats
```

| Impl. | Rôle |
|---|---|
| `UsbVendorTransport` | pyusb, réclame **interface 2** (laisse le CDC à l'OS), EP2 OUT/IN. Portage de `open_box()` + thread lecteur `PONG`/`STATUS`/`LOG`/`FAULT`. Helper `handshake()` (RESYNC→PING→SET_CONFIG→SET_MODE) + contrôle de version sur le `PONG`. |
| `MockTransport` | Fausse box en process : décode `DATA_SAMPLES` → ring buffer que la GUI trace (et/ou `.wav`), synthétise des `STATUS_REP` plausibles. Fait tourner **toute l'app + GUI sans matériel**. |
| *(futur)* `RfDongleTransport` | Phase 4 firmware, même interface. |

`transport/protocol.py` = portage **bit-identique** de
`OpenPulse-firmware/tools/pc_sender/proto.py` (framing, CRC-8, pack des structs)
+ les `assert` d'auto-test (`_CONFIG.size == 12`, `_STATS.size == 40`,
`crc8("123456789") == 0xF4`). Règle de synchro documentée : *toute modif de
`src/link/proto.h` se répercute ici dans le même commit* (même règle que les
`tools/` du firmware).

---

## 5. Conception haptique mono-actionneur

### 5.1 Le « langage vibratoire »

Un seul LRA → impossible de séparer spatialement « kerb avant-gauche » de
« blocage arrière ». On distingue les effets par leur **texture** :

| Effet | Bande de modulation | Caractère |
|---|---|---|
| road | 5–40 Hz, bruité | fond lisse, « la voiture roule » |
| engine | 8–30 Hz, périodique | ronronnement lent |
| abs | ~15 Hz, régulier | battement propre |
| lockup | 12–18 Hz, rugueux | grondement qui traîne |
| wheelspin | 25–35 Hz, bourdon | frétillement aigu |
| tc | ~20 Hz | micro-saccade brève |
| kerb | 45–60 Hz, dur | crépitement |
| rev-limiter | 7–10 Hz, dur | butée qui tape |
| gearshift | transitoire 40 ms | un coup sec |
| impact | transitoire 40–120 ms | choc |

Une **table de mixage par défaut** (niveaux relatifs) et l'ordre de
priorité/ducking sont livrés dans le profil `ac-default` et documentés dans
`docs/EFFECTS.md`. L'utilisateur ajuste au goût.

### 5.2 Budget de latence (côté PC)

| Étape | Coût typique | Note |
|---|---|---|
| Poll mém. partagée | 2–3 ms | borné par l'intervalle de poll (333–500 Hz) |
| Réception UDP | ~10 ms | jitter réseau/OS, pire que la mém. partagée |
| ZOH télémétrie → DSP | ≤ 1 bloc (2–16 ms) | |
| Génération DSP d'un bloc | ≪ 1 ms | numpy |
| **Buffer lookahead** | **8–60 ms (défaut ~20)** | **knob dominant**, compromis jitter ↔ latence |
| `dev.write()` USB | ~0,1 ms | mesuré firmware : 88 µs |
| Pipeline firmware (trame → jouable) | ~1,7 ms | mesuré, BENCH.md |
| Montée mécanique LRA | 10–20 ms | incompressible |

→ Ressenti total ≈ **35–55 ms** en mém. partagée / lookahead 20 ms, dont ~la
moitié est mécanique + buffer réglable. Windows : `timeBeginPeriod(1)` au
démarrage pour un `sleep` à 1 ms.

---

## 6. Orchestration & threads — `openpulse/core/`

| Thread | Rôle | Cadence |
|---|---|---|
| **T1 acquisition** | `source.poll()` → normalise → `state.telemetry.set(frame)` (swap atomique) ; alimente le `Recorder` | mém.part. ~500 Hz / UDP à l'arrivée |
| **T2 DSP + sortie** | lit la dernière frame (ZOH), applique les commandes GUI en attente, `pipeline.generate()`, `renderer` → `transport.send()` | boucle serrée `perf_counter`, blocs 2–16 ms |
| **T3 retour box** | `transport.frames_in()` → parse → `state.device` (stats, PONG, faults), journal | bloquant + timeout |
| **GUI** (principal) | dearpygui, boucle de rendu manuelle ; lit les snapshots, pousse des commandes | ~60 fps |

**Échange d'état** (`core/state.py`) :
- `state.telemetry` : dernière `TelemetryFrame` (référence swappée, lecture sans
  lock).
- `state.commands` : `queue.Queue` de commandes GUI → T2 (changement de gain,
  toggle d'effet, `SET_CONFIG` device, changement de profil…).
- `state.snapshot` : `Snapshot` (dataclass) publié ~60 Hz par T2/T3, lu par la
  GUI. Contient : télémétrie affichable, meters par effet, forme d'onde de
  sortie (ring ~2 s), stats device, RTT, faults.
- **Toutes** les mutations dearpygui sur le thread GUI. Les workers ne touchent
  jamais un widget.

**Arrêt propre** : signal → T2 envoie `SET_MODE(IDLE)` → `transport.close()`
(release interface) → join des threads (daemon). `timeEndPeriod(1)`.

Pourquoi threads + files et pas asyncio : pyusb est bloquant, dearpygui a sa
propre boucle, et le motif de pacing du sender de référence (déjà validé au
banc) est synchrone. Threads + `queue` = plus simple et éprouvé ici.

---

## 7. Configuration & profils — `openpulse/config/`

- Modèles **pydantic** : `AppSettings` (VID/PID, type de transport, hôte/port
  UDP, prefs UI), `DeviceConfig` (`sample_rate_hz`, `od_clamp`, `ol_lra_period`,
  `failsafe_ms`, `amp_max`, `loss_policy`, `underrun_decay_ms`), `MixerConfig`,
  `EffectParams` par effet, `Profile` (nom, jeu, motif d'association voiture,
  `device`, `mixer`, `effects{}`).
- Stockage : `%APPDATA%/OpenPulse/` (`settings.toml`, `profiles/*.toml`).
  Profils par défaut embarqués, copiés au premier lancement (`ac-default`,
  `ac-gt3`, `ac-formula`, …).
- **Résolution de profil** : à la connexion télémétrie, lire `carModel`
  (mém. part.) / `carName` (handshake UDP) → profil dont le motif (glob/regex)
  matche, sinon défaut du jeu. Override manuel dans la GUI. `Enregistrer` /
  `Enregistrer sous` / `Dupliquer` / `Revenir`.
- **Garde-fous device** : bornage aux plages proto (`link_control.c`). *Avertir*
  (sans bloquer) si `od_clamp > 0xC0` (thermique) ou `sample_rate_hz > 2000`
  (inutile, BENCH.md). Afficher en clair la fréquence pour `ol_lra_period`
  (`f = 1e6/(olp·98,46)`) et la tension crête pour `od_clamp`
  (`≈ 21,6 mV·od_clamp`). `ol_lra_period` derrière un volet « Avancé (matériel) »
  avec l'avertissement HARDWARE.md — c'est une constante mesurée.

---

## 8. GUI dearpygui — `openpulse/gui/`

Boucle de rendu manuelle :
`while dpg.is_dearpygui_running(): pull_snapshot(); update_widgets(); dpg.render_dearpygui_frame()`.
Panneaux (fenêtres dockables) :

1. **Connexion** — device (état, `fw x.y`, `proto vN` + ✓/✗, mode courant),
   transport (USB / Mock), source télémétrie (Auto / Mém. partagée / UDP +
   hôte:port), jeu + voiture + statut piste. Boutons Connecter / Déconnecter /
   Rescan. Écran d'aide Zadig si l'interface 2 n'est pas réclamable.
2. **Moniteur live** — scope de la forme d'onde de sortie (~2 s) + overlay des
   contributions par effet ; afficheurs vitesse/régime/rapport, barres
   throttle/brake, 4 cadrans de slip, vecteur G, pastilles de flags ; stats
   device (fifo, ovr/unr, crc, gap, backlog, `failsafe_trips`, VBAT, OVER_TEMP/OC
   en rouge, RTT).
3. **Effets** — table : `[✓]` nom | gain | seuil(s) | fréq | courbe | meter de
   contribution live | bouton **Test**. Groupés lit / événements. « Tout
   couper », presets.
4. **Mixeur / Master** — master gain, plafond du lit, limiteur (genou, ceiling),
   noise gate (floor, hold, hystérésis), garde thermique (constante de temps,
   seuil, foldback), gros VU de sortie + voyant d'écrêtage + jauge « budget
   thermique ».
5. **Config device** — `sample_rate_hz`, `failsafe_ms`, `amp_max`,
   `loss_policy`, `underrun_decay_ms` ; volet Avancé : `od_clamp` (+ ~V),
   `ol_lra_period` (+ ~Hz, warning). Bouton **Appliquer** → `SET_CONFIG`
   (option : reset stats). Diff visuel app ↔ box.
6. **Profils** — liste, charger/enregistrer/dupliquer, association voiture,
   import/export, auto-switch on/off.
7. **Diagnostics** — journal box (`LOG` colorés par niveau), `FAULT` décodés,
   compteurs proto, mini-bench (PING RTT n=100, « envoyer pattern test »,
   « forcer IDLE »), trace des trames brutes (toggle),
   **enregistrement/rejeu de télémétrie** (start/stop/charger + timeline).

---

## 9. Enregistrement / rejeu & mode simulateur

- `telemetry/record.py` : `TelemetryRecorder` écrit les `TelemetryFrame`
  (colonnes `.npz` ou msgpack) + méta (jeu, voiture, session).
  `TelemetryPlayer` **est une `TelemetrySource`** qui rejoue un fichier en
  temps réel (ou ×k, ou en boucle).
- `SyntheticSource` : `TelemetrySource` pilotée par des sliders / scénarios
  scriptés (« hotlap », « bloque l'avant », « tape un kerb », « part en
  tête-à-queue ») → régler et démontrer les effets **sans le jeu**.
- `tools/telemetry_capture.py` : dump live AC (les 2 sources) vers un
  enregistrement, pour les fixtures de test.
- `tools/waveform_preview.py` : passe un enregistrement dans le pipeline
  hors-ligne → `.wav` + PNG, pour l'A/B de réglage et la non-régression.

---

## 10. Tests (sans matériel, sans jeu) — `tests/`

| Fichier | Couvre |
|---|---|
| `test_protocol.py` | vecteur CRC `"123456789"→0xF4`, tailles structs (2/5/12/40/5), build/parse round-trip, trames malformées rejetées, pack `config_msg` (reprend l'intention de `firmware/tools/test/`) |
| `test_ac_shared_memory.py` / `test_ac_udp.py` | parse de dumps fixtures → champs bruts, endianness, tolérance aux champs ajoutés en queue |
| `test_normalize.py` | AC brut (2 sources) → `TelemetryFrame` équivalents sur une session fixture commune ; conversions d'unités ; `CapabilitySet` |
| `test_derive.py` | vitesse suspension, `lock_ratio`, détection impact/jerk, `gear_changed` sur séquences synthétiques |
| `test_effects.py` | par effet : borné `0..1` ; muet si désactivé ; muet si trigger absent ; RMS/pic dans la bande attendue si déclenché ; fréquence de modulation dominante (FFT) dans la bande déclarée ; continuité de phase entre blocs (pas de saut) |
| `test_mixer.py` | limiteur jamais > 1,0 ; gate engage/relâche avec hystérésis ; ducking atténue le lit ; foldback thermique réduit le master sous charge soutenue ; OVER_TEMP → 0 |
| `test_renderer.py` | quantification, tailles de batch, lookahead maintenu, maths du pacing, télémétrie périmée → rampe à 0 |
| `test_pipeline_integration.py` | `MockTransport` + lap enregistré → N s → aucune exception, cadence de trames dans la tolérance, SEQ monotone, sortie bornée |

CI : `pytest` pur Python. Fixtures commitées dans `tests/fixtures/`.

---

## 11. Arborescence proposée

> Phase A a livré `transport/` (`protocol.py`, `base.py`, `usb.py`, `mock.py`),
> `box/session.py`, `core/clock.py`, `streamer.py`, `controller.py`, `config.py`,
> `waveforms.py`, `gui/app.py`, `cli.py`, `app.py`. Le reste ci-dessous est la
> cible.

```
OpenPulse-app/
├── pyproject.toml                   Python ≥ 3.11
├── README.md
├── docs/
│   ├── ARCHITECTURE.md              (ce document)
│   ├── EFFECTS.md                   le langage vibratoire + catalogue + réglage
│   ├── TELEMETRY.md                 maps AC mém. partagée + UDP, schéma normalisé
│   └── SETUP-WINDOWS.md             libusb / Zadig / WinUSB
├── src/openpulse/
│   ├── app.py                       entrypoint : câble tout, possède les threads
│   ├── core/       state.py  clock.py  logging.py
│   ├── config/     models.py  store.py  defaults/*.toml
│   ├── telemetry/
│   │   ├── base.py  frame.py  registry.py  derive.py  record.py
│   │   └── ac/      shared_memory.py  udp.py  structs.py  normalize.py
│   ├── engine/
│   │   ├── effect.py  dsp.py  mixer.py  pipeline.py
│   │   └── effects/ road.py kerb.py lockup.py wheelspin.py abs_pulse.py
│   │                tc_cut.py gearshift.py engine_rpm.py rev_limiter.py
│   │                impact.py offtrack.py
│   ├── output/     renderer.py  thermal.py
│   ├── transport/  base.py  protocol.py  usb_vendor.py  mock.py
│   └── gui/        main_window.py  panels/*.py  widgets/*.py
├── tests/          fixtures/  test_*.py
└── tools/          telemetry_capture.py  waveform_preview.py
```

Emplacement : `OpenPulse-app/` à côté de `OpenPulse-firmware/`. Même dépôt ou
dépôt séparé — à décider (§14 Q3).

---

## 12. Packaging & plateforme

- Deps : `dearpygui`, `pyusb`, `numpy`, `pydantic`, `msgpack` (ou stdlib),
  `tomllib`. Dev : `pytest`, `ruff`, `mypy`.
- **Windows** (cible principale sim racing) : embarquer `libusb-1.0.dll`. Bind
  **WinUSB/libusbK via Zadig sur l'interface 2**, une fois — guidé par l'app,
  documenté. Disparaît quand firmware 3.6 (MS OS 2.0) atterrit.
- Le port CDC (`COMx`, lignes `[R]`) est *optionnel* : `TYPE_LOG` + `STATUS_REP`
  couvrent déjà le besoin. On peut l'ouvrir en lecture seule pour un onglet
  diagnostic.
- Distribution : `pyinstaller` one-folder → `OpenPulse.exe` (pas en phase 1).
- **Linux / macOS** : `libusb-package` fournit le backend ; sous Linux une règle
  udev peut être nécessaire pour réclamer l'interface sans privilèges. AC tourne
  surtout sous Windows — le cas Linux/macOS vise le PC « box » qui reçoit la
  **télémétrie UDP** d'un autre PC (la mém. partagée, elle, suppose le jeu
  local ; sous Proton elle vit dans le préfixe wine).

---

## 13. Feuille de route (incréments livrables)

Style ROADMAP firmware : chaque phase compile, tourne, et a un critère « fait ».

### Phase A — Squelette + transport (parité avec `openpulse_send.py`)  —  ✅ FAIT
- `transport/protocol.py` (miroir bit-identique de `proto.h` + auto-tests),
  `transport/base.py` (`Transport` ABC), `transport/usb.py` (`UsbVendorTransport`
  + backend `libusb-package`), `transport/mock.py` (`MockTransport`, modèle
  comportemental de la box).
- `box/session.py` (`BoxSession` : handshake, version-gate, thread lecteur, RTT),
  `core/clock.py` (résolution timer OS + `Pacer`), `streamer.py`
  (`SampleStreamer`, boucle de pacing temps-réel), `config.py` (`DeviceConfig`
  pydantic aux plages de `link_control.c`), `waveforms.py`.
- `openpulse-cli` — streamer headless, parité avec `openpulse_send.py`
  (`--transport usb|mock`, `--wave`, `--list`, dashboard, Ctrl-C → IDLE).
- GUI dearpygui (`gui/app.py`, `controller.py`) : panneaux **Connexion**
  (transport, VID/PID, `sample_rate_hz`/`failsafe_ms`, waveform, start/stop) +
  **Diagnostics** (table STATUS, RTT min/p50/p95, FAULT décodés, journal box,
  bouton Ping×50).
- **Tests** (`pytest`, 100 % sans matériel) : `test_protocol`, `test_mock`,
  `test_session`, `test_streamer`, `test_controller`, `test_gui_smoke`.
- **Fait** : box (réelle ou mock) pilotée depuis l'app, dashboard live, contrôle
  de version au `PONG` (`ProtocolMismatch`), arrêt propre (`SET_MODE IDLE`).

### Phase B — Télémétrie Assetto Corsa
- `TelemetryFrame`, `TelemetrySource` ABC + `CapabilitySet`,
  `ac/shared_memory.py`, `ac/udp.py`, `ac/normalize.py`, `derive.py`.
- `tools/telemetry_capture.py` + fixtures + `test_ac_*` / `test_normalize` /
  `test_derive`.
- GUI : panneau **Moniteur** (télémétrie brute + normalisée).
- **Fait** : les 2 sources produisent des `TelemetryFrame` équivalents sur une
  vraie session AC, vérifié sur fixtures.

### Phase C — Moteur d'effets v1
- `Effect` ABC, `dsp.py`, `pipeline.py`, `mixer.py`, les ~11 effets du catalogue,
  `record.py` rejeu, `SyntheticSource`, `tools/waveform_preview.py`.
- GUI : panneaux **Effets** + **Mixeur** + scope de sortie.
- Tests golden par effet sur enregistrements.
- **Fait** : rouler dans AC → rumble cohérent, effets distinguables au ressenti,
  aucun écrêtage dur, gate propre à l'arrêt.

### Phase D — Profils + garde thermique + finition
- `thermal.py`, limiteur/gate finaux, profils par voiture (auto-switch sur
  `carModel`), panneau **Config device** (`SET_CONFIG` + garde-fous),
  packaging PyInstaller, `docs/SETUP-WINDOWS.md`.
- **Fait** : usage réel sur plusieurs voitures, thermiquement sûr, installable
  par un tiers.

### Phase E — Extensibilité (valider l'abstraction)
- 2ᵉ jeu (p. ex. ACC, ou rFactor 2 / iRacing) → prouve que seule une
  `TelemetrySource` + un normaliseur suffisent.
- SimHub comme source de télémétrie alternative.
- Mode `ENVELOPE` (utile pour la future RF, Phase 4 firmware).
- Anticiper le multi-actionneur (le proto gagnerait un octet de canal, la FIFO
  deviendrait par canal — déjà noté dans ROADMAP firmware « Deferred »).

---

## 14. Points ouverts

**Actés** (cf. en-tête) : « DMA » = mémoire partagée officielle d'AC · dépôt
séparé · multi-OS (Windows/Linux/macOS) · libs tierces OK.

**Restent à trancher avant Phase C** :

1. **Périmètre effets v1** : le catalogue §4.3 te convient ? Priorités si on doit
   en couper ?
2. **Échantillons négatifs (anti-phase)** : autorisés pour un edge d'impact net,
   ou strictement `0..127` en v1 ?
3. **Mono-actionneur** : on ne prévoit rien pour le multi-canal maintenant (juste
   ne pas se fermer la porte) ?
4. **SimHub** : gardé en Phase E comme *une source parmi d'autres*, ou prioritaire ?
5. **Synchro `proto`** : les deux dépôts étant séparés, la synchro
   `transport/protocol.py` ↔ `firmware/src/link/proto.h` est manuelle (règle
   documentée + auto-tests de tailles/CRC). Suffisant, ou on veut un script de
   vérification qui pointe vers un checkout du firmware ?
