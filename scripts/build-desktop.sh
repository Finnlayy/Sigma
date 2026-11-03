#!/usr/bin/env bash
# Produces real, self-contained Ubuntu x64 packages. No first-launch pip/npm/apt.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${SIGMA_BUILD_PYTHON:-.venv/bin/python}"
mkdir -p build/kraken build/redis build/redis-src build/browsers .cache
REDIS_VERSION=7.2.7
ARCHIVE=".cache/redis-${REDIS_VERSION}.tar.gz"
curl --fail --location --retry 3 "https://codeload.github.com/redis/redis/tar.gz/refs/tags/${REDIS_VERSION}" -o "$ARCHIVE"
echo "fdbdee0a19a12e61c2eca46f4162f6d3ede3a01e0baa8a12cee1f35f3391f1e9  $ARCHIVE" | sha256sum --check
tar -xzf "$ARCHIVE" -C build/redis-src --strip-components=1
make -C build/redis-src -j2 BUILD_TLS=no MALLOC=libc REDIS_CFLAGS='' REDIS_LDFLAGS=''
cp build/redis-src/src/redis-server build/redis/
cp build/redis-src/COPYING build/redis/
# Official CLI version matching app/execution/kraken_cli_registry.json.
KRAKEN_ARCHIVE=.cache/kraken-cli-x86_64-unknown-linux-gnu.tar.gz
curl --fail --location --retry 3 https://github.com/krakenfx/kraken-cli/releases/download/v0.4.1/kraken-cli-x86_64-unknown-linux-gnu.tar.gz -o "$KRAKEN_ARCHIVE"
echo "a9a91782d00065a12800b5d3d585ff59fb30e87b321070e181d044e1909e1482  $KRAKEN_ARCHIVE" | sha256sum --check
tar -xzf "$KRAKEN_ARCHIVE" -C build/kraken --strip-components=1
build/kraken/kraken --version
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/browsers" "$PYTHON" -m playwright install chromium
npm run build
"$PYTHON" -m PyInstaller --noconfirm --distpath build/backend --workpath build/pyinstaller desktop/backend.spec
# Catch missing frozen modules/data before distributing any installer.
SIGMA_SMOKE_FROZEN=1 "$PYTHON" scripts/smoke-desktop.py
npm run desktop:pack
(cd release && sha256sum ./*.deb ./*.AppImage > SHA256SUMS)
