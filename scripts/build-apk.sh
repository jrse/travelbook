#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$ROOT_DIR/community/travelbook"
DIST_DIR="$ROOT_DIR/dist"
COPY_TO_DIST=1
SKIP_CHECKSUM=0

for arg in "$@"; do
  case "$arg" in
    --no-copy)
      COPY_TO_DIST=0
      ;;
    --skip-checksum)
      SKIP_CHECKSUM=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--no-copy] [--skip-checksum]" >&2
      exit 1
      ;;
  esac
done

if ! command -v abuild >/dev/null 2>&1; then
  echo "abuild is required but was not found in PATH." >&2
  exit 1
fi

if [[ ! -d "$PKG_DIR" ]]; then
  echo "Package directory not found: $PKG_DIR" >&2
  exit 1
fi

cd "$PKG_DIR"

if [[ "$SKIP_CHECKSUM" -ne 1 ]]; then
  abuild checksum
fi

abuild -r

if [[ "$COPY_TO_DIST" -eq 1 ]]; then
  mkdir -p "$DIST_DIR"
  find "$HOME/packages" -type f -name 'travelbook-*.apk' -exec cp {} "$DIST_DIR"/ \;
  echo "Copied built APKs to: $DIST_DIR"
fi

echo "Build completed in: $PKG_DIR"
