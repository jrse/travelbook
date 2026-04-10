#!/usr/bin/env bash
set -euo pipefail

APP_NAME="travelbook"
DEFAULT_DIST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/dist"
DIST_DIR="$DEFAULT_DIST_DIR"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    *)
      DIST_DIR="$arg"
      ;;
  esac
done

if [[ ! -d "$DIST_DIR" ]]; then
  echo "dist directory not found: $DIST_DIR" >&2
  exit 1
fi

latest_apk="$({ ls -1 "$DIST_DIR"/"$APP_NAME"-*.apk 2>/dev/null || true; } | sort -V | tail -n 1)"

if [[ -z "$latest_apk" ]]; then
  echo "No APK found in $DIST_DIR matching $APP_NAME-*.apk" >&2
  exit 1
fi

APK_CMD=(apk add --allow-untrusted --upgrade "$latest_apk")

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Selected latest APK: $latest_apk"
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  "${APK_CMD[@]}"
else
  if command -v doas >/dev/null 2>&1; then
    doas "${APK_CMD[@]}"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "${APK_CMD[@]}"
  else
    echo "Root privileges required. Run as root or install doas/sudo." >&2
    exit 1
  fi
fi

echo "Installed/updated from: $latest_apk"
