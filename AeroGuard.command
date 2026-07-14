#!/bin/bash
# AeroGuard one-click launcher (macOS). Double-click in Finder:
# installs Docker Desktop if missing, starts it if needed, brings the
# stack up, then opens the HMI in a chromeless app window with the API
# key pre-loaded.
set -euo pipefail
cd "$(dirname "$0")"

# Docker Desktop bundles its CLI here; covers a just-installed copy
# whose /usr/local/bin symlink does not exist yet.
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"

fail() {
  osascript -e "display dialog \"$1\" buttons {\"OK\"} with icon stop with title \"AeroGuard\"" >/dev/null 2>&1 || true
  echo "ERROR: $1"
  exit 1
}

if [ ! -d "/Applications/Docker.app" ] && ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop not found — downloading it now (one time, ~700 MB)..."
  case "$(uname -m)" in
    arm64) DMG_URL="https://desktop.docker.com/mac/main/arm64/Docker.dmg" ;;
    *)     DMG_URL="https://desktop.docker.com/mac/main/amd64/Docker.dmg" ;;
  esac
  DMG="${TMPDIR:-/tmp}/AeroGuard-Docker.dmg"
  curl -fL -o "$DMG" "$DMG_URL" \
    || fail "Could not download Docker Desktop. Check the network, or install it from docker.com and run this again."
  echo "Installing Docker Desktop..."
  MOUNT=$(hdiutil attach "$DMG" -nobrowse | awk -F'\t' '/\/Volumes\//{print $NF; exit}')
  [ -n "$MOUNT" ] || fail "Could not open the Docker Desktop installer image."
  cp -R "$MOUNT/Docker.app" /Applications/ \
    || { hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true; fail "Could not copy Docker.app into /Applications."; }
  hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
  rm -f "$DMG"
fi

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop..."
  echo "(first run only: accept the service agreement in the Docker window —"
  echo " this launcher continues automatically once Docker is ready)"
  open -a Docker || fail "Could not start Docker Desktop."
  for _ in $(seq 1 150); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  docker info >/dev/null 2>&1 \
    || fail "Docker did not become ready. Finish Docker Desktop's first-run setup, then run this again."
fi

[ -f .env ] || echo "AEROGUARD_API_KEY=$(openssl rand -base64 32)" > .env

# Pick the host port. If this copy's stack is already up, reuse the port
# it is bound to; otherwise take the first free one starting at 8000, so
# several copies of the repo can run side by side.
if [ -n "$(docker compose ps -q 2>/dev/null)" ]; then
  PORT=$(docker compose port aeroguard 8000 2>/dev/null | awk -F: '{print $NF}')
  PORT=${PORT:-8000}
else
  PORT=8000
  while nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; do PORT=$((PORT + 1)); done
fi
export AEROGUARD_PORT="$PORT"
[ "$PORT" = "8000" ] || echo "Port 8000 is busy — using port $PORT for this copy."

echo "Starting AeroGuard (first run builds the image — several minutes)..."
docker compose up -d \
  || fail "docker compose failed. Inspect with: docker compose logs"

echo "Waiting for the backend to become healthy..."
for _ in $(seq 1 90); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 \
  || fail "Backend did not become healthy. Inspect with: docker compose logs"

KEY=$(grep '^AEROGUARD_API_KEY=' .env | cut -d= -f2-)
URL="http://127.0.0.1:$PORT/#key=$KEY"

if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL"
elif [ -d "/Applications/Microsoft Edge.app" ]; then
  open -na "Microsoft Edge" --args --app="$URL"
else
  open "$URL"
fi

echo "AeroGuard is running. Closing the window leaves the server up;"
echo "stop it with: docker compose down"
