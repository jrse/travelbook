# travelbook (erste Version)

GTK-App fuer postmarketOS mit:
- GPS-Ortung via GeoClue
- Automatischer Wechsel zwischen pedestrian mode und drive mode anhand der GPS-Geschwindigkeit
- OSM-POI-Suche ueber Overpass API mit dynamischem Radius; Staedte sind standardmaessig als POIs aktiv, im drive mode werden nur Staedte gezeigt
- Profilseite mit aktivierbaren POI-Kategorien
- Scrollbarer Radar-Graph: aktuelle Position im Zentrum, POIs radial nach Distanz
- Navigation zu Staedten mit separater POI-Tabelle fuer Ziele in der ausgewaehlten Stadt
- Automatische Aktualisierung bei Positionsaenderung
- App-Icon und Desktop-Entry enthalten

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
