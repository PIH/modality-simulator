#!/usr/bin/env bash
# Builds the image, checks that it refuses to start without its required setting, then that it
# starts and answers /health. Run by CI, and worth running before tagging a release.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-modality-simulator:smoke}"
docker build -t "$IMAGE" .

echo "==> Without MODALITY_SIMULATOR_GATEWAY_AE it exits 2 and says why"
set +e
output=$(docker run --rm "$IMAGE" 2>&1)
status=$?
set -e
[ "$status" -eq 2 ] || { echo "expected exit status 2, got $status: $output"; exit 1; }
grep -q "MODALITY_SIMULATOR_GATEWAY_AE must be set" <<< "$output" || { echo "no explanation: $output"; exit 1; }

echo "==> With it, the console answers /health"
name="modality-simulator-smoke-$$"
docker run -d --name "$name" -e MODALITY_SIMULATOR_GATEWAY_AE=SMOKE_GW -p 127.0.0.1::8080 "$IMAGE" >/dev/null
trap 'docker rm -f "$name" >/dev/null' EXIT
port=$(docker port "$name" 8080/tcp | head -1 | sed 's/.*://')
for _ in $(seq 30); do
    if curl -fsS "http://127.0.0.1:$port/health"; then
        echo
        echo "==> OK"
        exit 0
    fi
    sleep 1
done
docker logs "$name"
exit 1
