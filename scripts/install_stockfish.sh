#!/usr/bin/env bash
# Download the official Stockfish Linux x86-64 release binary into engines/.
#
# Used by the Render build (no root, so no apt) and works the same locally.
# Tries the AVX2 build first and falls back to the portable SSE4.1/POPCNT
# build if the AVX2 binary does not answer `uci` on this CPU.
#
#   STOCKFISH_RELEASE   release tag (default sf_17.1)
#   STOCKFISH_ARCH      force one build, e.g. x86-64-sse41-popcnt
#   STOCKFISH_DEST      output path (default <repo>/engines/stockfish-bin)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE="${STOCKFISH_RELEASE:-sf_17.1}"
DEST="${STOCKFISH_DEST:-$REPO_ROOT/engines/stockfish-bin}"
ARCHES="${STOCKFISH_ARCH:-x86-64-avx2 x86-64-sse41-popcnt}"

smoke() {
  # A working engine answers `uci` with `uciok`.
  printf 'uci\nquit\n' | timeout 20 "$1" 2>/dev/null | grep -q '^uciok'
}

if [ -x "$DEST" ] && smoke "$DEST"; then
  echo "stockfish already installed at $DEST: $(printf 'uci\nquit\n' | "$DEST" | head -1)"
  exit 0
fi

mkdir -p "$(dirname "$DEST")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for arch in $ARCHES; do
  asset="stockfish-ubuntu-${arch}"
  url="https://github.com/official-stockfish/Stockfish/releases/download/${RELEASE}/${asset}.tar"
  echo "downloading $url"
  if ! curl -fsSL --retry 3 -o "$TMP/$asset.tar" "$url"; then
    echo "download failed for $arch" >&2
    continue
  fi
  tar -xf "$TMP/$asset.tar" -C "$TMP" "stockfish/$asset"
  chmod +x "$TMP/stockfish/$asset"
  if smoke "$TMP/stockfish/$asset"; then
    mv "$TMP/stockfish/$asset" "$DEST"
    echo "installed $RELEASE ($arch) at $DEST: $(printf 'uci\nquit\n' | "$DEST" | head -1)"
    exit 0
  fi
  echo "$arch build does not run on this CPU; trying next" >&2
done

echo "could not install a working Stockfish binary" >&2
exit 1
