#!/usr/bin/env bash
# Forced command for the CI deploy key (see deploy/setup-vps.sh). CI connects with
#
#   ssh sjekka-deploy@vps <commit-sha> <<< "$GHCR_TOKEN"
#
# so the only thing that key can do is deploy an image this repository built.
# Starts ghcr.io/h44k0n/sjekka:<commit-sha>, waits for the container's
# healthcheck, and rolls back to the previous image if it never becomes healthy.
set -euo pipefail

IMAGE_REPO=ghcr.io/h44k0n/sjekka
cd "${SJEKKA_DIR:-/opt/sjekka}"

sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: deploy <40-character commit sha>" >&2
    exit 2
fi
image="$IMAGE_REPO:$sha"
previous="$(cat state/deployed-image 2>/dev/null || true)"

# Log in to GHCR with the short-lived token from stdin, in a throwaway config
# so no credentials stay on the server.
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
docker login ghcr.io -u github-actions --password-stdin > /dev/null

start() {
    SJEKKA_IMAGE="$1" docker compose up -d --remove-orphans
}

wait_healthy() {
    local container status=missing
    container="$(docker compose ps -q app)"
    for _ in $(seq 30); do
        status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo missing)"
        [ "$status" = "healthy" ] && return 0
        sleep 2
    done
    echo "Container did not become healthy (last status: $status)" >&2
    docker compose logs --tail 50 app >&2
    return 1
}

SJEKKA_IMAGE="$image" docker compose pull app
start "$image"

if wait_healthy; then
    echo "$image" > state/deployed-image
    docker image prune -f > /dev/null
    echo "Deployed $image"
    exit 0
fi

if [ -n "$previous" ]; then
    echo "Rolling back to $previous" >&2
    start "$previous"
    wait_healthy || echo "Rollback is unhealthy too" >&2
fi
exit 1
