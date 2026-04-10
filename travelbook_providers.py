import math
from typing import Optional, Tuple

try:
    from pydbus import SystemBus
except Exception:  # pragma: no cover - optional runtime dependency
    SystemBus = None

from travelbook_core import APP_ID, COMPASS_HEADING_OFFSET_DEG, GEOCLUE_ACCURACY_LEVEL_EXACT, GPS_FIX_MAX_ACCURACY_M

GPS_SOURCE_HINTS = ("gps", "gnss", "nmea", "satellite")
NON_GPS_SOURCE_HINTS = ("wifi", "wlan", "cell", "3gpp", "network", "ip", "geocode")


def is_valid_gps_fix(description: Optional[str], accuracy_m: Optional[float]) -> bool:
    normalized = (description or "").strip().lower()
    if normalized and any(hint in normalized for hint in NON_GPS_SOURCE_HINTS):
        return False
    if accuracy_m is None or not math.isfinite(accuracy_m) or accuracy_m <= 0:
        return bool(normalized and any(hint in normalized for hint in GPS_SOURCE_HINTS))
    if accuracy_m > GPS_FIX_MAX_ACCURACY_M:
        return False
    if not normalized:
        return True
    return any(hint in normalized for hint in GPS_SOURCE_HINTS)


class GeoClueProvider:
    def __init__(self) -> None:
        self._bus = None
        self._manager = None
        self._client = None
        self.last_error: Optional[str] = None
        try:
            if SystemBus is None:
                raise RuntimeError("pydbus nicht verfuegbar")
            self._bus = SystemBus()
            self._manager = self._bus.get("org.freedesktop.GeoClue2", "/org/freedesktop/GeoClue2/Manager")
        except Exception:
            self._bus = None
            self._manager = None
            self.last_error = "GeoClue nicht verfuegbar"

    def _ensure_client(self):
        if self._client is not None or self._manager is None or self._bus is None:
            return
        client_path = self._manager.GetClient()
        self._client = self._bus.get("org.freedesktop.GeoClue2", client_path)
        self._client.DesktopId = APP_ID
        if hasattr(self._client, "RequestedAccuracyLevel"):
            self._client.RequestedAccuracyLevel = GEOCLUE_ACCURACY_LEVEL_EXACT
        self._client.DistanceThreshold = 5
        self._client.TimeThreshold = 2
        self._client.Start()
        self.last_error = None

    def get_location(self) -> Optional[Tuple[float, float]]:
        try:
            self._ensure_client()
            if self._client is None or self._bus is None:
                self.last_error = "GeoClue Client konnte nicht gestartet werden"
                return None
            location_path = self._client.Location
            location = self._bus.get("org.freedesktop.GeoClue2", location_path)
            latitude = float(location.Latitude)
            longitude = float(location.Longitude)
            accuracy = float(location.Accuracy) if hasattr(location, "Accuracy") else None
            description = str(location.Description) if hasattr(location, "Description") else ""
            if not is_valid_gps_fix(description, accuracy):
                detail = description or "unbekannte Quelle"
                if accuracy is None or not math.isfinite(accuracy):
                    self.last_error = f"Nur unbestaetigte Ortung erhalten ({detail}), kein GPS-Fix"
                else:
                    self.last_error = f"Ortung verworfen: {detail}, Genauigkeit {int(accuracy)} m"
                return None
            self.last_error = None
            return latitude, longitude
        except Exception:
            self.last_error = "GPS-Abfrage ueber GeoClue fehlgeschlagen"
            return None


class CompassProvider:
    def __init__(self) -> None:
        self._bus = None
        self._proxy = None
        self._claimed = False
        self._available = False
        self.last_error: Optional[str] = None
        try:
            self._bus = SystemBus()
            self._proxy = self._bus.get("net.hadess.SensorProxy", "/net/hadess/SensorProxy/Compass")
            self._available = self._detect_availability()
        except Exception:
            self._bus = None
            self._proxy = None
            self.last_error = "SensorProxy nicht verfuegbar"

    def _detect_availability(self) -> bool:
        if self._proxy is None:
            return False
        try:
            if hasattr(self._proxy, "HasCompass"):
                return bool(self._proxy.HasCompass)
            return False
        except Exception:
            return False

    def is_available(self) -> bool:
        if self._proxy is None:
            return False
        self._available = self._detect_availability()
        return self._available

    def _ensure_claimed(self) -> None:
        if self._claimed or self._proxy is None:
            return
        if not self.is_available():
            return
        if hasattr(self._proxy, "ClaimCompass"):
            self._proxy.ClaimCompass()
            self._claimed = True
            self.last_error = None

    def get_heading(self) -> Optional[float]:
        try:
            self._ensure_claimed()
            if self._proxy is None or not hasattr(self._proxy, "CompassHeading"):
                self.last_error = "CompassHeading nicht verfuegbar"
                return None
            heading = float(self._proxy.CompassHeading)
            if not math.isfinite(heading) or heading < 0:
                self.last_error = "Ungueltiger Kompasswert"
                return None
            self.last_error = None
            return (heading + COMPASS_HEADING_OFFSET_DEG) % 360.0
        except Exception:
            self.last_error = "Kompassabfrage fehlgeschlagen"
            return None

    def close(self) -> None:
        if self._proxy is None or not self._claimed:
            return
        try:
            if hasattr(self._proxy, "ReleaseCompass"):
                self._proxy.ReleaseCompass()
        except Exception:
            pass
        self._claimed = False
