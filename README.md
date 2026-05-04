# travelbook (erste Version)

GTK-App fuer postmarketOS mit:
- GPS-Ortung via GeoClue
- Automatischer Wechsel zwischen pedestrian mode und drive mode anhand der GPS-Geschwindigkeit
- Manueller Wechsel zwischen pedestrian mode und drive mode direkt im Radar
- OSM-POI-Suche ueber Overpass API mit dynamischem Radius; Staedte sind standardmaessig als POIs aktiv, im drive mode werden nur Staedte gezeigt
- Profilseite mit aktivierbaren POI-Kategorien
- Scrollbarer Radar-Graph: aktuelle Position im Zentrum, POIs radial nach Distanz
- Navigation zu Staedten mit separater POI-Tabelle fuer Ziele in der ausgewaehlten Stadt
- Automatische Aktualisierung bei Positionsaenderung
- Tagebuch mit lokaler Audioaufnahme ueber Bluetooth-Mikrofon, MP3-Konvertierung und Whisper-Transkription
- Optionale Nachbearbeitung von Tagebuchtext ueber Ollama
- App-Icon und Desktop-Entry enthalten

## Audio-Tagebuch

Fuer die Sprachaufnahme erwartet `travelbook`:
- `parec` aus PulseAudio bzw. PipeWire-Pulse
- `ffmpeg` fuer WAV-zu-MP3-Konvertierung
- Ein verfuegbares Bluetooth-Mikrofon; falls nur A2DP aktiv ist, versucht die App auf HFP/HSP umzuschalten

Ablauf:
1. Im Tagebuch-Editor `Aufnahme starten` waehlen.
2. Die App nimmt ueber das erkannte Bluetooth-Mikrofon auf.
3. Nach `Aufnahme stoppen` wird das Audio lokal als MP3 konvertiert.
4. Das MP3 wird an den konfigurierten Whisper-Endpunkt geschickt und der erkannte Text in den Editor uebernommen.

Die Whisper-Basis-URL wird in der Profilseite gespeichert. Bei einer nackten Host-URL wie `http://192.168.178.153:8000` verwendet die App automatisch den Endpunkt `/transcribe`.

## RNNoise-Modell

Wenn ein RNNoise-Modell vorhanden ist, aktiviert `travelbook` beim MP3-Export die `ffmpeg`-Filterkette `arnndn` zur Rauschminderung.

Unterstuetzte Suchpfade:
- `/usr/share/ffmpeg/librnnoise.rnnn`
- `/usr/share/ffmpeg/rnnoise.rnnn`
- `/usr/share/rnnoise/rnnoise-model.rnnn`
- `/usr/share/rnnoise/model.rnnn`

Das Hilfsskript installiert ein Standardmodell nach `/usr/share/ffmpeg/rnnoise.rnnn`:

```bash
./scripts/install-rnnoise-model.sh
```

Optional kann ein eigener Zielpfad uebergeben werden:

```bash
./scripts/install-rnnoise-model.sh /usr/share/rnnoise/model.rnnn
```

## Build als APK (auf Alpine/aports-Umgebung)

1. Fertige Struktur liegt bereits unter `community/travelbook/`.
2. Build-Skript ausführen:
```bash
./scripts/build-apk.sh
```
3. Optional direkt manuell:
```bash
cd community/travelbook
abuild checksum
abuild -r
```
4. Installieren mit `apk add --allow-untrusted /path/to/travelbook-0.1.0-r0.apk`

Optionen für das Build-Skript:
```bash
./scripts/build-apk.sh --skip-checksum
./scripts/build-apk.sh --no-copy
```

## Install/Update aus `dist/`

1. Neueste APK nach `dist/` legen (Beispiel):
```bash
cp /home/user/packages/community/aarch64/travelbook-*.apk ./dist/
```
2. Install/Update starten:
```bash
./scripts/install-update.sh
```

Optional kann ein anderer Ordner uebergeben werden:
```bash
./scripts/install-update.sh /pfad/zu/dist
```

Nur die ausgewaehlte neueste APK anzeigen:
```bash
./scripts/install-update.sh --dry-run
```

## Start

```bash
travelbook
```

Wenn kein GPS verfuegbar ist, kann im Profil eine manuelle Fallback-Position gesetzt werden.
