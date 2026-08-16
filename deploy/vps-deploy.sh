#!/usr/bin/env bash
set -Eeuo pipefail

readonly release_image="${1:?release image is required}"
readonly release_commit="${2:?release commit is required}"
readonly app_dir="${BBP_APP_DIR:-/opt/baidu-buzz-proxy}"
readonly expected_image="ghcr.io/dactdma/baidu-buzz-proxy:sha-${release_commit}"

if [[ ! "$release_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid release commit" >&2
    exit 1
fi

if [[ "$release_image" != "$expected_image" ]]; then
    echo "Unexpected release image" >&2
    exit 1
fi

cd "$app_dir"

if [[ ! -d .git || ! -f .env ]]; then
    echo "The repository or .env file is missing in $app_dir" >&2
    exit 1
fi

previous_image=""
previous_commit=""
[[ -f .deployed-image ]] && previous_image="$(<.deployed-image)"
[[ -f .deployed-commit ]] && previous_commit="$(<.deployed-commit)"

write_image_env() {
    local image="$1"
    local temporary_file=".image.env.tmp"
    printf 'BBP_IMAGE=%s\n' "$image" > "$temporary_file"
    chmod 600 "$temporary_file"
    mv -f "$temporary_file" .image.env
}

compose() {
    docker compose \
        --env-file .env \
        --env-file .image.env \
        -f compose.prod.yaml \
        "$@"
}

check_health() {
    local http_port
    http_port="$(sed -n 's/^BBP_HTTP_PORT=//p' .env | tail -n 1)"
    http_port="${http_port:-8080}"
    curl \
        --fail \
        --silent \
        --show-error \
        --retry 10 \
        --retry-delay 2 \
        "http://127.0.0.1:${http_port}/api/health" \
        >/dev/null
}

backup_database() {
    local backup_name
    backup_name="app-$(date -u +%Y%m%dT%H%M%SZ).db"
    compose exec -T -e "BBP_BACKUP_NAME=$backup_name" app python - <<'PY'
import os
import sqlite3
from pathlib import Path

source_path = Path("/app/data/app.db")
if source_path.exists():
    backup_directory = Path("/app/data/backups")
    backup_directory.mkdir(exist_ok=True)
    backup_path = backup_directory / os.environ["BBP_BACKUP_NAME"]
    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    for old_backup in sorted(backup_directory.glob("app-*.db"))[:-7]:
        old_backup.unlink()
PY
}

if [[ -n "$previous_image" && -f compose.prod.yaml ]]; then
    write_image_env "$previous_image"
    if compose ps --services --status running | grep -qx app; then
        backup_database
    fi
fi

git fetch --quiet origin main
git cat-file -e "${release_commit}^{commit}"
git checkout --quiet --detach "$release_commit"

docker pull "$release_image"
write_image_env "$release_image"

if compose up -d --remove-orphans --wait --wait-timeout 420 && check_health; then
    printf '%s\n' "$release_image" > .deployed-image
    printf '%s\n' "$release_commit" > .deployed-commit
    echo "Deployment completed: $release_commit"
    exit 0
fi

echo "Deployment failed" >&2
compose logs --no-color --tail 100 app nginx >&2 || true

if [[ -n "$previous_image" && "$previous_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Rolling back to $previous_commit" >&2
    git checkout --quiet --detach "$previous_commit"
    write_image_env "$previous_image"
    docker pull "$previous_image"
    compose up -d --remove-orphans --wait --wait-timeout 420
    check_health
    echo "Rollback completed" >&2
fi

exit 1
