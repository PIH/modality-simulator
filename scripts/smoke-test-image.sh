#!/usr/bin/env bash
# Builds the image, checks that it refuses to start without its stations file, then that it starts
# with one and answers /health. Run by CI, and worth running before tagging a release.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-modality-simulator:smoke}"
docker build -t "$IMAGE" .

echo "==> Without a stations file it exits 2 and says why"
set +e
output=$(docker run --rm "$IMAGE" 2>&1)
status=$?
set -e
[ "$status" -eq 2 ] || { echo "expected exit status 2, got $status: $output"; exit 1; }
grep -q "No stations file at /config/stations.yaml" <<< "$output" || { echo "no explanation: $output"; exit 1; }

echo "==> With one, the console answers /health and opens on its station"
config=$(mktemp -d)
cat > "$config/stations.yaml" <<'YAML'
gateway: {ae: SMOKE_GW}
stations:
  - {name: X-ray, calling_ae: SMOKE_CR, modalities: [CR]}
YAML
chmod -R a+rX "$config"
name="modality-simulator-smoke-$$"
docker run -d --name "$name" -v "$config":/config:ro -p 127.0.0.1::8080 "$IMAGE" >/dev/null
trap 'docker rm -f "$name" >/dev/null; rm -rf "$config"' EXIT
port=$(docker port "$name" 8080/tcp | head -1 | sed 's/.*://')
for _ in $(seq 30); do
    if curl -fsS "http://127.0.0.1:$port/health"; then
        echo
        redirect=$(curl -s -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:$port/")
        [[ "$redirect" == */station/SMOKE_CR ]] || { echo "/ redirected to '$redirect'"; exit 1; }
        echo "==> OK"
        exit 0
    fi
    sleep 1
done
docker logs "$name"
exit 1
