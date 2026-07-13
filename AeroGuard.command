#!/bin/bash
# AeroGuard one-click launcher (macOS). Double-click in Finder:
# starts Docker if needed, brings the stack up, then opens the HMI
# in a chromeless app window with the API key pre-loaded.
set -euo pipefail
cd "$(dirname "$0")"

fail() {
  osascript -e "display dialog \"$1\" buttons {\"OK\"} with icon stop with title \"AeroGuard\"" >/dev/null 2>&1 || true
  echo "ERROR: $1"
  exit 1
}

command -v docker >/dev/null 2>&1 \
  || fail "Docker Desktop is not installed. Get it from docker.com, then run this again."

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop..."
  open -a Docker || fail "Could not start Docker Desktop."
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  docker info >/dev/null 2>&1 \
    || fail "Docker did not become ready. Start Docker Desktop manually, then run this again."
fi

[ -f .env ] || echo "AEROGUARD_API_KEY=$(openssl rand -base64 32)" > .env

echo "Starting AeroGuard (first run builds the image — several minutes)..."
docker compose up -d

echo "Waiting for the backend to become healthy..."
for _ in $(seq 1 90); do
  curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1 \
  || fail "Backend did not become healthy. Inspect with: docker compose logs"

KEY=$(grep '^AEROGUARD_API_KEY=' .env | cut -d= -f2-)
URL="http://127.0.0.1:8000/#key=$KEY"

if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL"
elif [ -d "/Applications/Microsoft Edge.app" ]; then
  open -na "Microsoft Edge" --args --app="$URL"
else
  open "$URL"
fi

echo "AeroGuard is running. Closing the window leaves the server up;"
echo "stop it with: docker compose down"
