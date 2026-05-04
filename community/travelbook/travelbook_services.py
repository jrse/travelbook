import json
import logging
import math
import shutil
import subprocess
import time
import uuid
from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, IO, List, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

from travelbook_core import (
    APP_ID,
    CITY_POI_FILTERS,
    DBSCAN_EPSILON_M,
    DBSCAN_MIN_POINTS,
    DIARY_APP_VERSION,
    DRIVE_MODE_AVG_WINDOW_SECS,
    DRIVE_MODE_BASE_RADIUS_M,
    DRIVE_MODE_MAX_RADIUS_M,
    DRIVE_MODE_MIN_AVG_SPEED_MPS,
    GPS_SPEED_MIN_MOVE_M,
    MAX_VISIBLE_POI_RESULTS,
    OLLAMA_BASE_URL_DEFAULT,
    OLLAMA_CONNECT_TIMEOUT_SECS,
    OLLAMA_DIARY_MAX_TOKENS,
    OLLAMA_DIARY_MODEL,
    OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT,
    OLLAMA_READ_TIMEOUT_SECS,
    POI_FETCH_BACKOFF_SECS,
    POI_FETCH_RETRYABLE_STATUS_CODES,
    POI_FETCH_RETRY_COUNT,
    POI_FETCH_TIMEOUT_SECS,
    POI_REFRESH_INTERVAL_MAX_SECS,
    POI_REFRESH_INTERVAL_MIN_SECS,
    POI_REFRESH_INTERVAL_PARTIAL_MOVE_FACTOR,
    POI_REFRESH_MAX_MOVE_M,
    POI_REFRESH_MIN_MOVE_M,
    POI_REFRESH_RADIUS_FACTOR,
    POI_SPEED_EXTENSION_MIN_MPS,
    POI_SPEED_LOOKAHEAD_SECS,
    RECORDER_CHANNELS,
    RECORDER_SAMPLE_RATE,
    RNNOISE_MODEL_CANDIDATES,
    TRAVEL_HEADING_MIN_MOVE_M,
    WHISPER_BASE_URL_DEFAULT,
    WHISPER_CONNECT_TIMEOUT_SECS,
    WHISPER_READ_TIMEOUT_SECS,
    Cluster,
    Poi,
    build_overpass_query,
    parse_filter,
)


class PoiFetchError(RuntimeError):
    def __init__(self, user_message: str, *, retryable: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.retryable = retryable


LOGGER = logging.getLogger(__name__)


class DiaryImproveError(RuntimeError):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class AudioRecordingError(RuntimeError):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class AudioTranscriptionError(RuntimeError):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class AudioRecordingSession:
    wav_path: Path
    mp3_path: Path
    source_name: Optional[str]
    source_label: str
    card_name: Optional[str]
    previous_card_profile: Optional[str]
    switched_card_profile: bool
    process: subprocess.Popen
    wav_handle: IO[bytes]
    started_at: float


OVERPASS_FILTER_BATCH_SIZE = 24


def _chunk_filters(filters: List[str], chunk_size: int) -> List[List[str]]:
    return [filters[index:index + chunk_size] for index in range(0, len(filters), chunk_size)]


def _post_overpass_query(
    lat: float,
    lon: float,
    radius: int,
    filters: List[str],
    http_post,
    sleep_fn,
) -> Dict:
    query = build_overpass_query(lat, lon, radius, filters)
    LOGGER.warning(
        "POI query lat=%s lon=%s radius=%s filter_count=%s filters=%s\n%s",
        lat,
        lon,
        radius,
        len(filters),
        filters,
        query,
    )
    data = None
    last_error: Optional[PoiFetchError] = None
    attempts = POI_FETCH_RETRY_COUNT + 1
    for attempt in range(attempts):
        try:
            response = http_post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
                headers={
                    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
                    "User-Agent": f"{APP_ID}/poi-fetch",
                },
                timeout=POI_FETCH_TIMEOUT_SECS,
            )
            response_text = str(getattr(response, "text", "") or "")
            LOGGER.warning(
                "POI response status=%s body=%s",
                getattr(response, "status_code", "unknown"),
                response_text[:1000].strip(),
            )
            response.raise_for_status()
            data = response.json()
            break
        except requests.Timeout:
            last_error = PoiFetchError(
                "POI-Abfrage hat das Zeitlimit erreicht. Bitte erneut versuchen.",
                retryable=True,
            )
        except requests.ConnectionError:
            last_error = PoiFetchError(
                "Keine Netzwerkverbindung für POI-Abfrage.",
                retryable=True,
            )
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            response_text = str(getattr(getattr(exc, "response", None), "text", "") or "").strip()
            if response_text:
                LOGGER.warning("POI query failed status=%s response=%s", status_code, response_text[:1000])
            if status_code in POI_FETCH_RETRYABLE_STATUS_CODES:
                last_error = PoiFetchError(
                    f"POI-Dienst antwortet gerade nicht stabil (HTTP {status_code}).",
                    retryable=True,
                )
            elif status_code == 406 and len(filters) > 1:
                midpoint = max(1, len(filters) // 2)
                left = _post_overpass_query(lat, lon, radius, filters[:midpoint], http_post, sleep_fn)
                right = _post_overpass_query(lat, lon, radius, filters[midpoint:], http_post, sleep_fn)
                return {"elements": left.get("elements", []) + right.get("elements", [])}
            elif status_code == 406:
                last_error = PoiFetchError(
                    "POI-Abfrage wurde vom Dienst abgelehnt (HTTP 406).",
                    retryable=False,
                )
            elif status_code is not None:
                last_error = PoiFetchError(
                    f"POI-Abfrage wurde vom Dienst abgelehnt (HTTP {status_code}).",
                    retryable=False,
                )
            else:
                last_error = PoiFetchError("POI-Abfrage ist mit einem HTTP-Fehler fehlgeschlagen.", retryable=False)
        except (ValueError, json.JSONDecodeError):
            last_error = PoiFetchError("POI-Dienst lieferte eine ungueltige Antwort.", retryable=False)
        except requests.RequestException:
            last_error = PoiFetchError("POI-Abfrage ist aufgrund eines Netzwerkfehlers fehlgeschlagen.", retryable=True)

        if last_error is None:
            break
        if not last_error.retryable or attempt >= attempts - 1:
            raise last_error
        if attempt < len(POI_FETCH_BACKOFF_SECS):
            sleep_fn(POI_FETCH_BACKOFF_SECS[attempt])

    if data is None:
        raise last_error or PoiFetchError("POIs konnten nicht geladen werden.", retryable=False)
    if not isinstance(data, dict):
        raise PoiFetchError("POI-Dienst lieferte ein ungueltiges Datenformat.", retryable=False)
    return data


def resolve_region(lat: float, lon: float, http_get=requests.get) -> Dict[str, str]:
    response = http_get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": lat,
            "lon": lon,
            "zoom": 10,
            "addressdetails": 1,
        },
        headers={"User-Agent": f"{APP_ID}/region-lookup"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    address = data.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or "-"
    )
    region = address.get("state") or address.get("county") or address.get("region") or city
    country = address.get("country") or "-"
    wiki_topic = region if region and region != "-" else city
    if not wiki_topic or wiki_topic == "-":
        wiki_topic = country
    wiki_url = f"https://en.wikipedia.org/wiki/{quote(str(wiki_topic).replace(' ', '_'))}"
    return {"city": str(city), "region": str(region), "country": str(country), "wiki_url": wiki_url}


def diary_file_path(base_dir: Path, day: date) -> Path:
    return base_dir / f"{day.isoformat()}.json"


def settings_file_path(base_dir: Path) -> Path:
    return base_dir / "settings.json"


def load_app_settings(base_dir: Path) -> Dict[str, str]:
    defaults = {
        "ollama_base_url": OLLAMA_BASE_URL_DEFAULT,
        "ollama_diary_system_prompt": OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT,
        "whisper_base_url": WHISPER_BASE_URL_DEFAULT,
    }
    path = settings_file_path(base_dir)
    if not path.exists():
        return defaults.copy()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults.copy()
    if not isinstance(payload, dict):
        return defaults.copy()
    settings = defaults.copy()
    for key in defaults:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            settings[key] = value.strip()
    return settings


def save_app_settings(base_dir: Path, settings: Dict[str, str]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ollama_base_url": str(settings.get("ollama_base_url", OLLAMA_BASE_URL_DEFAULT)).strip(),
        "ollama_diary_system_prompt": str(
            settings.get("ollama_diary_system_prompt", OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT)
        ).strip(),
        "whisper_base_url": str(settings.get("whisper_base_url", WHISPER_BASE_URL_DEFAULT)).strip(),
    }
    settings_file_path(base_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def whisper_transcribe_url(base_url: str) -> str:
    normalized = (base_url or WHISPER_BASE_URL_DEFAULT).strip().rstrip("/")
    if not normalized:
        normalized = WHISPER_BASE_URL_DEFAULT
    tail = normalized.rsplit("/", 1)[-1].lower()
    if tail in {"transcribe", "transcription"}:
        return normalized
    scheme_split = normalized.split("://", 1)
    path_part = scheme_split[1] if len(scheme_split) == 2 else scheme_split[0]
    if "/" in path_part:
        return normalized
    return f"{normalized}/transcribe"


def _run_audio_command(command: List[str], command_runner=subprocess.run) -> subprocess.CompletedProcess:
    return command_runner(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def discover_rnnoise_model() -> Optional[Path]:
    for candidate in RNNOISE_MODEL_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _parse_pactl_blocks(text: str, header_prefix: str) -> List[List[str]]:
    blocks: List[List[str]] = []
    current: List[str] = []
    for line in text.splitlines():
        if line.startswith(header_prefix):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_pactl_key_values(block: List[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in block:
        stripped = raw_line.strip()
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            values[key] = value.strip()
        elif " = " in stripped:
            key, value = stripped.split(" = ", 1)
            values[key] = value.strip().strip('"')
    return values


def list_audio_sources(command_runner=subprocess.run) -> List[Dict[str, str]]:
    try:
        result = _run_audio_command(["pactl", "list", "sources"], command_runner=command_runner)
    except (OSError, subprocess.SubprocessError):
        return []
    sources: List[Dict[str, str]] = []
    for block in _parse_pactl_blocks(result.stdout, "Source #"):
        values = _parse_pactl_key_values(block)
        if values:
            sources.append(values)
    return sources


def list_audio_cards(command_runner=subprocess.run) -> List[Dict[str, str]]:
    try:
        result = _run_audio_command(["pactl", "list", "cards"], command_runner=command_runner)
    except (OSError, subprocess.SubprocessError):
        return []
    cards: List[Dict[str, str]] = []
    for block in _parse_pactl_blocks(result.stdout, "Card #"):
        values = _parse_pactl_key_values(block)
        if values:
            cards.append(values)
    return cards


def discover_bluetooth_input_source(command_runner=subprocess.run) -> Optional[str]:
    sources = list_audio_sources(command_runner=command_runner)
    LOGGER.info("Audio source discovery: found %s detailed sources", len(sources))
    for source in sources:
        source_name = source.get("Name", "").strip()
        source_class = source.get("device.class", "").strip().lower()
        device_api = source.get("device.api", "").strip().lower()
        description = source.get("Description", "").strip()
        LOGGER.info(
            "Audio source candidate name=%s description=%s class=%s api=%s",
            source_name or "-",
            description or "-",
            source_class or "-",
            device_api or "-",
        )
        if not source_name:
            continue
        if source_class == "monitor" or ".monitor" in source_name.lower():
            continue
        if device_api == "bluez" or source_name.lower().startswith("bluez_input."):
            LOGGER.info("Audio source discovery selected bluetooth source=%s", source_name)
            return source_name

    try:
        result = _run_audio_command(["pactl", "list", "short", "sources"], command_runner=command_runner)
    except (OSError, subprocess.SubprocessError):
        LOGGER.warning("Audio source discovery failed: unable to query short sources")
        return None

    for raw_line in result.stdout.splitlines():
        columns = raw_line.split("\t")
        if len(columns) < 2:
            continue
        source_name = columns[1].strip()
        lowered = source_name.lower()
        LOGGER.info("Audio short source candidate name=%s", source_name)
        if ".monitor" in lowered:
            continue
        if "bluez_input" in lowered:
            LOGGER.info("Audio short source selected bluetooth source=%s", source_name)
            return source_name
    LOGGER.warning("Audio source discovery found no bluetooth microphone source")
    return None


def describe_audio_input_source(source_name: Optional[str], command_runner=subprocess.run) -> str:
    if not source_name:
        return "Standardmikrofon"
    try:
        result = command_runner(
            ["pactl", "list", "sources"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        result = None

    if result is not None:
        current_name = None
        current_description = None
        for raw_line in result.stdout.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("Name: "):
                current_name = stripped.split("Name: ", 1)[1].strip()
                current_description = None
                continue
            if stripped.startswith("Description: "):
                current_description = stripped.split("Description: ", 1)[1].strip()
            if current_name == source_name and current_description:
                return current_description

    label = source_name
    if source_name.startswith("bluez_input."):
        label = source_name.split("bluez_input.", 1)[1]
    label = label.replace(".monitor", "")
    label = label.replace(".", " ")
    label = label.replace("_", " ")
    return label.strip() or source_name


def ensure_bluetooth_input_source(command_runner=subprocess.run) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    current_source = discover_bluetooth_input_source(command_runner=command_runner)
    if current_source:
        for card in list_audio_cards(command_runner=command_runner):
            active_profile = card.get("Active Profile")
            card_name = card.get("Name")
            if card_name and card_name.startswith("bluez_card."):
                return current_source, card_name, active_profile, False
        return current_source, None, None, False

    cards = list_audio_cards(command_runner=command_runner)
    LOGGER.info("Bluetooth input fallback: inspecting %s cards", len(cards))
    for card in cards:
        card_name = card.get("Name", "").strip()
        active_profile = card.get("Active Profile", "").strip()
        alias = card.get("bluez.alias", "").strip() or card.get("device.description", "").strip()
        if not card_name.startswith("bluez_card."):
            continue
        LOGGER.info(
            "Bluetooth card candidate name=%s alias=%s active_profile=%s",
            card_name,
            alias or "-",
            active_profile or "-",
        )
        if active_profile == "handsfree_head_unit":
            continue
        profiles_blob = "\n".join(f"{key}: {value}" for key, value in card.items())
        if "handsfree_head_unit" not in profiles_blob:
            continue
        try:
            LOGGER.warning(
                "Switching bluetooth card %s (%s) from profile %s to handsfree_head_unit for microphone recording",
                card_name,
                alias or "-",
                active_profile or "-",
            )
            _run_audio_command(["pactl", "set-card-profile", card_name, "handsfree_head_unit"], command_runner=command_runner)
        except (OSError, subprocess.SubprocessError):
            LOGGER.exception("Failed to switch bluetooth card profile for %s", card_name)
            continue
        time.sleep(1.0)
        current_source = discover_bluetooth_input_source(command_runner=command_runner)
        if current_source:
            LOGGER.info("Bluetooth input became available after profile switch: source=%s", current_source)
            return current_source, card_name, active_profile or None, True
    LOGGER.warning("No bluetooth microphone source is available after profile checks")
    return None, None, None, False


def start_audio_recording(
    output_dir: Path,
    *,
    source_name: Optional[str] = None,
    popen_factory=subprocess.Popen,
    command_runner=subprocess.run,
    time_fn=time.time,
) -> AudioRecordingSession:
    parec_path = shutil.which("parec")
    if not parec_path:
        raise AudioRecordingError("`parec` ist nicht installiert.")

    output_dir.mkdir(parents=True, exist_ok=True)
    recording_id = str(uuid.uuid4())
    wav_path = output_dir / f"{recording_id}.wav"
    mp3_path = output_dir / f"{recording_id}.mp3"
    previous_card_profile = None
    card_name = None
    switched_card_profile = False
    if source_name:
        selected_source = source_name
    else:
        selected_source, card_name, previous_card_profile, switched_card_profile = ensure_bluetooth_input_source(
            command_runner=command_runner
        )
    if not selected_source:
        LOGGER.warning("Audio recording start aborted: no bluetooth microphone source available")
        raise AudioRecordingError(
            "Kein Bluetooth-Mikrofon verfügbar. Aktuell ist vermutlich nur A2DP aktiv; bitte Headset/HFP aktivieren."
        )
    source_label = describe_audio_input_source(selected_source, command_runner=command_runner)
    LOGGER.warning(
        "Audio recording start source=%s label=%s card=%s previous_profile=%s switched=%s",
        selected_source,
        source_label,
        card_name or "-",
        previous_card_profile or "-",
        switched_card_profile,
    )
    wav_handle = wav_path.open("wb")
    command = [
        parec_path,
        "--record",
        "--format=s16le",
        f"--rate={RECORDER_SAMPLE_RATE}",
        f"--channels={RECORDER_CHANNELS}",
        "--file-format=wav",
        "--client-name=travelbook",
        "--stream-name=travelbook-diary",
    ]
    if selected_source:
        command.append(f"--device={selected_source}")

    try:
        process = popen_factory(command, stdout=wav_handle, stderr=subprocess.PIPE)
    except OSError as exc:
        wav_handle.close()
        try:
            wav_path.unlink()
        except FileNotFoundError:
            pass
        raise AudioRecordingError("Audioaufnahme konnte nicht gestartet werden.") from exc

    return AudioRecordingSession(
        wav_path=wav_path,
        mp3_path=mp3_path,
        source_name=selected_source,
        source_label=source_label,
        card_name=card_name,
        previous_card_profile=previous_card_profile,
        switched_card_profile=switched_card_profile,
        process=process,
        wav_handle=wav_handle,
        started_at=float(time_fn()),
    )


def stop_audio_recording(
    session: AudioRecordingSession,
    *,
    command_runner=subprocess.run,
    timeout_secs: float = 10.0,
) -> Path:
    LOGGER.warning(
        "Audio recording stop requested source=%s wav=%s mp3=%s",
        session.source_name or "-",
        session.wav_path,
        session.mp3_path,
    )
    try:
        if session.process.poll() is None:
            session.process.terminate()
        _, stderr_data = session.process.communicate(timeout=timeout_secs)
    except subprocess.TimeoutExpired as exc:
        session.process.kill()
        session.process.communicate()
        raise AudioRecordingError("Audioaufnahme liess sich nicht sauber beenden.") from exc
    finally:
        try:
            session.wav_handle.close()
        except Exception:
            pass

    restore_audio_card_profile(session, command_runner=command_runner)

    if session.process.returncode not in (0, -15, 143):
        stderr_text = ""
        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", errors="ignore").strip()
        elif isinstance(stderr_data, str):
            stderr_text = stderr_data.strip()
        message = "Audioaufnahme ist fehlgeschlagen."
        if stderr_text:
            message = f"Audioaufnahme ist fehlgeschlagen: {stderr_text}"
        raise AudioRecordingError(message)

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise AudioRecordingError("`ffmpeg` ist nicht installiert. MP3-Konvertierung nicht möglich.")

    wav_size = session.wav_path.stat().st_size if session.wav_path.exists() else -1
    LOGGER.warning(
        "Audio recording stopped returncode=%s wav_exists=%s wav_size_bytes=%s",
        session.process.returncode,
        session.wav_path.exists(),
        wav_size,
    )

    try:
        LOGGER.warning("Audio MP3 conversion start input=%s output=%s", session.wav_path, session.mp3_path)
        ffmpeg_command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(session.wav_path),
        ]
        rnnoise_model = discover_rnnoise_model()
        if rnnoise_model is not None:
            ffmpeg_command.extend(["-af", f"arnndn=model={rnnoise_model}"])
            LOGGER.warning("Audio RNNoise enabled model=%s", rnnoise_model)
        else:
            LOGGER.warning(
                "Audio RNNoise model not found; skipping denoise. checked=%s",
                ",".join(RNNOISE_MODEL_CANDIDATES),
            )
        ffmpeg_command.extend(
            [
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(session.mp3_path),
            ]
        )
        command_runner(
            ffmpeg_command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AudioRecordingError("MP3-Konvertierung ist fehlgeschlagen.") from exc

    mp3_size = session.mp3_path.stat().st_size if session.mp3_path.exists() else -1
    LOGGER.warning(
        "Audio MP3 conversion finished output=%s exists=%s size_bytes=%s",
        session.mp3_path,
        session.mp3_path.exists(),
        mp3_size,
    )
    return session.mp3_path


def cleanup_audio_recording(session: AudioRecordingSession) -> None:
    for path in (session.wav_path, session.mp3_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            continue


def restore_audio_card_profile(session: AudioRecordingSession, *, command_runner=subprocess.run) -> None:
    if not session.switched_card_profile or not session.card_name or not session.previous_card_profile:
        return
    try:
        LOGGER.warning(
            "Restoring bluetooth card %s to profile %s after recording",
            session.card_name,
            session.previous_card_profile,
        )
        _run_audio_command(
            ["pactl", "set-card-profile", session.card_name, session.previous_card_profile],
            command_runner=command_runner,
        )
    except (OSError, subprocess.SubprocessError):
        LOGGER.exception("Failed to restore bluetooth card profile for %s", session.card_name)


def transcribe_audio_file(
    audio_path: Path,
    *,
    base_url: str,
    http_post=requests.post,
) -> str:
    if not audio_path.exists():
        raise AudioTranscriptionError("Audiodatei wurde nicht gefunden.")

    request_url = whisper_transcribe_url(base_url)
    started_at = time.time()
    LOGGER.warning(
        "Whisper request url=%s file=%s size_bytes=%s",
        request_url,
        audio_path.name,
        audio_path.stat().st_size,
    )
    try:
        with audio_path.open("rb") as handle:
            response = http_post(
                request_url,
                files={"file": (audio_path.name, handle, "audio/mpeg")},
                headers={"User-Agent": f"{APP_ID}/audio-transcribe"},
                timeout=(WHISPER_CONNECT_TIMEOUT_SECS, WHISPER_READ_TIMEOUT_SECS),
            )
        response.raise_for_status()
    except requests.Timeout as exc:
        LOGGER.exception("Whisper request timeout url=%s", request_url)
        raise AudioTranscriptionError("Whisper hat nicht rechtzeitig geantwortet.") from exc
    except requests.ConnectionError as exc:
        LOGGER.exception("Whisper connection failed url=%s", request_url)
        raise AudioTranscriptionError("Keine Verbindung zum Whisper-Dienst.") from exc
    except requests.HTTPError as exc:
        LOGGER.exception(
            "Whisper HTTP error url=%s status=%s body=%s",
            request_url,
            getattr(getattr(exc, "response", None), "status_code", "?"),
            str(getattr(getattr(exc, "response", None), "text", "") or "")[:1000],
        )
        raise AudioTranscriptionError(
            f"Whisper hat die Anfrage abgelehnt (HTTP {getattr(getattr(exc, 'response', None), 'status_code', '?')})."
        ) from exc
    except requests.RequestException as exc:
        LOGGER.exception("Whisper request failed url=%s", request_url)
        raise AudioTranscriptionError("Whisper-Anfrage ist fehlgeschlagen.") from exc

    elapsed_ms = int((time.time() - started_at) * 1000)
    content_type = str(response.headers.get("Content-Type", "")).lower()
    LOGGER.warning(
        "Whisper response url=%s status=%s content_type=%s elapsed_ms=%s",
        request_url,
        getattr(response, "status_code", "?"),
        content_type or "-",
        elapsed_ms,
    )
    if "application/json" in content_type:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            LOGGER.exception("Whisper response JSON decode failed url=%s", request_url)
            raise AudioTranscriptionError("Whisper lieferte ungueltiges JSON.") from exc
        LOGGER.warning("Whisper JSON payload=%s", json.dumps(payload, ensure_ascii=False)[:2000])
        if isinstance(payload, dict):
            for key in ("text", "transcript", "result"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    LOGGER.warning("Whisper transcript text=%s", value.strip()[:2000])
                    return value.strip()
        raise AudioTranscriptionError("Whisper lieferte keinen Transkript-Text.")

    text = str(getattr(response, "text", "") or "").strip()
    if text:
        LOGGER.warning("Whisper text payload=%s", text[:2000])
        return text
    raise AudioTranscriptionError("Whisper lieferte keinen Transkript-Text.")


def ollama_generate_url(base_url: str) -> str:
    normalized = (base_url or OLLAMA_BASE_URL_DEFAULT).strip().rstrip("/")
    if normalized.endswith("/api"):
        return f"{normalized}/generate"
    return f"{normalized}/api/generate"


def improve_diary_entry(
    text: str,
    *,
    base_url: str,
    system_prompt: str,
    model: str = OLLAMA_DIARY_MODEL,
    http_post=requests.post,
) -> str:
    prompt = text.strip()
    if not prompt:
        raise DiaryImproveError("Tagebuchtext ist leer.")

    request_url = ollama_generate_url(base_url)
    request_payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt.strip() or OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT,
        "stream": True,
        "think": False,
        "options": {"num_predict": OLLAMA_DIARY_MAX_TOKENS},
    }
    LOGGER.warning("Ollama diary request url=%s payload=%s", request_url, json.dumps(request_payload, ensure_ascii=False))

    response = None
    try:
        response = http_post(
            request_url,
            json=request_payload,
            headers={
                "Accept": "application/x-ndjson,application/json;q=0.9,*/*;q=0.8",
                "User-Agent": f"{APP_ID}/diary-improve",
            },
            timeout=(OLLAMA_CONNECT_TIMEOUT_SECS, OLLAMA_READ_TIMEOUT_SECS),
            stream=True,
        )
        response.raise_for_status()
        parts: List[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            try:
                chunk = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise DiaryImproveError("Ollama lieferte ungültige Streaming-Daten.") from exc
            if not isinstance(chunk, dict):
                continue
            response_part = chunk.get("response")
            if isinstance(response_part, str) and response_part:
                parts.append(response_part)
            if chunk.get("done") is True:
                break
        improved = "".join(parts).strip()
        if not improved:
            raise DiaryImproveError("Ollama lieferte keinen überarbeiteten Tagebucheintrag.")
        LOGGER.warning("Ollama diary result=%s", improved)
        return improved
    except requests.Timeout as exc:
        raise DiaryImproveError("Ollama hat nicht rechtzeitig geantwortet.") from exc
    except requests.ConnectionError as exc:
        raise DiaryImproveError("Keine Verbindung zum Ollama-Dienst.") from exc
    except requests.HTTPError as exc:
        raise DiaryImproveError(
            f"Ollama hat die Anfrage abgelehnt (HTTP {getattr(getattr(exc, 'response', None), 'status_code', '?')})."
        ) from exc
    except requests.RequestException as exc:
        raise DiaryImproveError("Ollama-Anfrage ist fehlgeschlagen.") from exc
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def load_diary_entries(base_dir: Path, day: date) -> List[Dict]:
    path = diary_file_path(base_dir, day)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def save_diary_entries(
    base_dir: Path,
    day: date,
    entries: List[Dict],
    version: str = DIARY_APP_VERSION,
    timestamp: Optional[str] = None,
) -> None:
    now = timestamp or (datetime.utcnow().isoformat() + "Z")
    payload = {
        "date": day.isoformat(),
        "metadata": {
            "app": "travelbook",
            "version": version,
            "updated_at": now,
            "entry_count": len(entries),
        },
        "entries": entries,
    }
    path = diary_file_path(base_dir, day)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_category(tags: Dict[str, str]) -> str:
    for key in ["amenity", "tourism", "shop", "leisure", "highway", "place"]:
        if key in tags:
            return f"{key}:{tags[key]}"
    return "unknown"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def calculate_navigation_info(
    selected_poi: Optional[Poi],
    current_location,
    heading_deg: Optional[float],
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
    bearing_fn: Callable[[float, float, float, float], float] = bearing_deg,
):
    if selected_poi is None or current_location is None:
        return None

    distance = distance_fn(
        current_location[0],
        current_location[1],
        selected_poi.lat,
        selected_poi.lon,
    )
    bearing = bearing_fn(
        current_location[0],
        current_location[1],
        selected_poi.lat,
        selected_poi.lon,
    )
    turn = bearing if heading_deg is None else (bearing - heading_deg + 360.0) % 360.0
    return selected_poi, distance, bearing, heading_deg, turn


def derive_travel_heading(
    previous_location,
    current_location,
    min_move_m: float = TRAVEL_HEADING_MIN_MOVE_M,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
    bearing_fn: Callable[[float, float, float, float], float] = bearing_deg,
) -> Optional[float]:
    if previous_location is None or current_location is None:
        return None
    moved = distance_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )
    if moved < min_move_m:
        return None
    return bearing_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )


def calculate_speed_mps(
    previous_location,
    previous_ts: Optional[float],
    current_location,
    current_ts: Optional[float],
    min_move_m: float = GPS_SPEED_MIN_MOVE_M,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> Optional[float]:
    if previous_location is None or current_location is None or previous_ts is None or current_ts is None:
        return None
    delta_t = current_ts - previous_ts
    if delta_t <= 0:
        return None
    moved = distance_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )
    if moved < min_move_m:
        return 0.0
    return moved / delta_t


def trim_location_samples(
    samples: Sequence[Tuple[float, Tuple[float, float]]],
    max_age_secs: float = DRIVE_MODE_AVG_WINDOW_SECS,
    now_ts: Optional[float] = None,
) -> List[Tuple[float, Tuple[float, float]]]:
    if not samples:
        return []
    reference_ts = samples[-1][0] if now_ts is None else now_ts
    cutoff = reference_ts - max_age_secs
    return [(sample_ts, loc) for sample_ts, loc in samples if sample_ts >= cutoff]


def average_speed_mps(
    samples: Sequence[Tuple[float, Tuple[float, float]]],
    window_secs: float = DRIVE_MODE_AVG_WINDOW_SECS,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> Optional[float]:
    windowed = trim_location_samples(samples, window_secs)
    if len(windowed) < 2:
        return None
    start_ts, start_loc = windowed[0]
    end_ts, end_loc = windowed[-1]
    delta_t = end_ts - start_ts
    if delta_t <= 0:
        return None
    moved = distance_fn(start_loc[0], start_loc[1], end_loc[0], end_loc[1])
    return moved / delta_t


def detect_travel_mode(
    instant_speed_mps: Optional[float],
    avg_speed_window_mps: Optional[float],
    pedestrian_threshold_mps: float = DRIVE_MODE_MIN_AVG_SPEED_MPS,
) -> str:
    if avg_speed_window_mps is not None and avg_speed_window_mps >= pedestrian_threshold_mps:
        return "drive"
    if instant_speed_mps is not None:
        return "pedestrian"
    return "pedestrian"


def assign_clusters(
    pois: List[Poi],
    radius_m: int,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> List[Cluster]:
    if len(pois) < DBSCAN_MIN_POINTS:
        for poi in pois:
            poi.cluster_id = -1
        return []

    epsilon = min(DBSCAN_EPSILON_M, max(80.0, radius_m * 0.2))
    labels = [-2] * len(pois)

    def region_query(index: int) -> List[int]:
        result = []
        for other in range(len(pois)):
            if distance_fn(pois[index].lat, pois[index].lon, pois[other].lat, pois[other].lon) <= epsilon:
                result.append(other)
        return result

    cluster_id = 0
    for index in range(len(pois)):
        if labels[index] != -2:
            continue
        neighbors = region_query(index)
        if len(neighbors) < DBSCAN_MIN_POINTS:
            labels[index] = -1
            continue

        labels[index] = cluster_id
        seeds = neighbors[:]
        cursor = 0
        while cursor < len(seeds):
            seed_index = seeds[cursor]
            if labels[seed_index] == -1:
                labels[seed_index] = cluster_id
            if labels[seed_index] == -2:
                labels[seed_index] = cluster_id
                expanded = region_query(seed_index)
                if len(expanded) >= DBSCAN_MIN_POINTS:
                    for neighbor in expanded:
                        if neighbor not in seeds:
                            seeds.append(neighbor)
            cursor += 1
        cluster_id += 1

    grouped: Dict[int, List[Poi]] = {}
    for index, poi in enumerate(pois):
        poi.cluster_id = labels[index]
        if labels[index] >= 0:
            grouped.setdefault(labels[index], []).append(poi)

    clusters: List[Cluster] = []
    for cid, members in grouped.items():
        center_lat = sum(p.lat for p in members) / float(len(members))
        center_lon = sum(p.lon for p in members) / float(len(members))
        radius = 0.0
        for poi in members:
            radius = max(radius, distance_fn(center_lat, center_lon, poi.lat, poi.lon))
        clusters.append(
            Cluster(
                cluster_id=cid,
                center_lat=center_lat,
                center_lon=center_lon,
                radius_m=radius,
                size=len(members),
            )
        )
    return clusters


def match_filter(filter_lookup: Dict, tags: Dict[str, str]) -> Optional[str]:
    for key, value in tags.items():
        found = filter_lookup.get((key, value))
        if found is not None:
            return found
    return None


def extract_poi_url(tags: Dict[str, str]) -> Optional[str]:
    for key in ("website", "contact:website", "url", "contact:url"):
        value = str(tags.get(key, "")).strip()
        if not value:
            continue
        if value.startswith(("http://", "https://")):
            return value
        if value.startswith("www."):
            return f"https://{value}"
    return None


def poi_refresh_distance(radius_m: int) -> float:
    return min(POI_REFRESH_MAX_MOVE_M, max(POI_REFRESH_MIN_MOVE_M, float(radius_m) * POI_REFRESH_RADIUS_FACTOR))


def effective_query_radius(
    base_radius_m: int,
    max_radius_m: int,
    speed_mps: Optional[float],
    travel_mode: str = "pedestrian",
) -> int:
    if travel_mode == "drive":
        base_radius_m = max(base_radius_m, DRIVE_MODE_BASE_RADIUS_M)
        max_radius_m = max(max_radius_m, DRIVE_MODE_MAX_RADIUS_M)
    if speed_mps is None or speed_mps < POI_SPEED_EXTENSION_MIN_MPS:
        return min(base_radius_m, max_radius_m)
    extension = int(speed_mps * POI_SPEED_LOOKAHEAD_SECS)
    return min(max_radius_m, max(base_radius_m, base_radius_m + extension))


def poi_refresh_interval(radius_m: int, speed_mps: Optional[float]) -> float:
    if speed_mps is None or speed_mps <= 0:
        return POI_REFRESH_INTERVAL_MAX_SECS
    derived = poi_refresh_distance(radius_m) / speed_mps
    return min(POI_REFRESH_INTERVAL_MAX_SECS, max(POI_REFRESH_INTERVAL_MIN_SECS, derived))


def should_refresh_pois(
    current_location,
    reference_location,
    radius_m: int,
    speed_mps: Optional[float] = None,
    seconds_since_refresh: Optional[float] = None,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> bool:
    if current_location is None or reference_location is None:
        return True
    moved = distance_fn(
        current_location[0],
        current_location[1],
        reference_location[0],
        reference_location[1],
    )
    threshold = poi_refresh_distance(radius_m)
    if moved >= threshold:
        return True
    if seconds_since_refresh is None:
        return False
    partial_threshold = max(15.0, threshold * POI_REFRESH_INTERVAL_PARTIAL_MOVE_FACTOR)
    return seconds_since_refresh >= poi_refresh_interval(radius_m, speed_mps) and moved >= partial_threshold


def fetch_pois(
    lat: float,
    lon: float,
    radius: int,
    categories: Dict[str, bool],
    filter_lookup: Dict,
    category_labels: Dict[str, str],
    include_cities: bool = False,
    city_only: bool = False,
    http_post=requests.post,
    sleep_fn=time.sleep,
) -> List[Poi]:
    active_filters = [filter_key for filter_key, enabled in categories.items() if enabled]
    city_filter_lookup = {}
    for _label, filter_text in CITY_POI_FILTERS:
        parsed = parse_filter(filter_text)
        if parsed is not None:
            city_filter_lookup[parsed] = filter_text
    city_labels = {filter_text: label for label, filter_text in CITY_POI_FILTERS}
    city_filters = [filter_key for filter_key in active_filters if filter_key in city_labels]

    if city_only:
        active_filters = city_filters
        extra_filters = []
    else:
        extra_filters = [
            filter_key for _label, filter_key in CITY_POI_FILTERS if include_cities and filter_key not in active_filters
        ]

    if not active_filters and not extra_filters:
        return []

    filter_batches = _chunk_filters(active_filters, OVERPASS_FILTER_BATCH_SIZE)
    if not filter_batches:
        filter_batches = [[]]
    if extra_filters:
        filter_batches[-1] = filter_batches[-1] + extra_filters

    elements: List[Dict] = []
    for batch_filters in filter_batches:
        data = _post_overpass_query(lat, lon, radius, batch_filters, http_post, sleep_fn)
        elements.extend(data.get("elements", []))

    results: List[Poi] = []
    seen_elements = set()
    for element in elements:
        element_key = (element.get("type"), element.get("id"))
        if element_key in seen_elements:
            continue
        seen_elements.add(element_key)
        if "lat" in element and "lon" in element:
            poi_lat, poi_lon = float(element["lat"]), float(element["lon"])
        elif "center" in element:
            poi_lat, poi_lon = float(element["center"]["lat"]), float(element["center"]["lon"])
        else:
            continue

        distance = distance_m(lat, lon, poi_lat, poi_lon)
        if distance > radius:
            continue

        bearing = bearing_deg(lat, lon, poi_lat, poi_lon)
        tags = element.get("tags", {})
        filter_key = match_filter(filter_lookup, tags) or match_filter(city_filter_lookup, tags) or "unknown"
        if city_only and filter_key not in city_labels:
            continue
        name = tags.get("name", f"{element.get('type', 'poi')} #{element.get('id', '?')}")
        results.append(
            Poi(
                name=name,
                lat=poi_lat,
                lon=poi_lon,
                distance_m=distance,
                bearing_deg=bearing,
                category=infer_category(tags),
                category_filter=filter_key,
                category_label=category_labels.get(filter_key, city_labels.get(filter_key, "Andere")),
                url=extract_poi_url(tags),
            )
        )

    results.sort(key=lambda poi: poi.distance_m)
    return results[:MAX_VISIBLE_POI_RESULTS]


def is_city_poi(poi: Optional[Poi]) -> bool:
    if poi is None:
        return False
    return poi.category.startswith("place:") and poi.category_filter in {filter_text for _label, filter_text in CITY_POI_FILTERS}
