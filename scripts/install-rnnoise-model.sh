#!/usr/bin/env bash
set -euo pipefail

MODEL_URL="${MODEL_URL:-https://raw.githubusercontent.com/richardpl/arnndn-models/master/std.rnnn}"
TARGET_PATH="${1:-/usr/share/ffmpeg/rnnoise.rnnn}"
TARGET_DIR="$(dirname "$TARGET_PATH")"
TMP_FILE="$(mktemp)"

cleanup() {
  rm -f "$TMP_FILE"
}
trap cleanup EXIT

download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$MODEL_URL" -o "$TMP_FILE"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP_FILE" "$MODEL_URL"
    return
  fi
  echo "Neither curl nor wget is installed." >&2
  exit 1
}

install_file() {
  if [[ -w "$TARGET_DIR" ]] || [[ ! -e "$TARGET_DIR" && -w "$(dirname "$TARGET_DIR")" ]]; then
    mkdir -p "$TARGET_DIR"
    install -Dm644 "$TMP_FILE" "$TARGET_PATH"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo mkdir -p "$TARGET_DIR"
    sudo install -Dm644 "$TMP_FILE" "$TARGET_PATH"
    return
  fi
  echo "No write access to $TARGET_DIR and sudo is unavailable." >&2
  exit 1
}

download
install_file

echo "Installed RNNoise model to $TARGET_PATH"
