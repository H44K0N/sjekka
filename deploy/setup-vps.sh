#!/usr/bin/env bash
# One-time (and re-runnable) VPS setup for Sjekka. Run as root from a copy of
# the deploy/ directory plus compose.yaml:
#
#   sudo bash setup-vps.sh <ci-deploy-key.pub>
#
# - installs Docker from Docker's apt repository
# - creates the sjekka-deploy user, the only user allowed to run the app
# - installs compose.yaml and deploy.sh to /opt/sjekka
# - asks for the Gemini API key if /opt/sjekka/.env doesn't exist yet
# - authorizes the CI key, restricted to running deploy.sh
# - installs a nightly database backup (sjekka-backup.timer)
#
# Re-run it after changing compose.yaml or deploy.sh.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run with sudo" >&2; exit 1; }
pubkey_file="${1:?Usage: setup-vps.sh <ci-deploy-key.pub>}"
src="$(cd "$(dirname "$0")" && pwd)"
dir=/opt/sjekka
user=sjekka-deploy

if ! command -v docker > /dev/null; then
    echo "==> Installing Docker"
    apt-get update -q
    apt-get install -y -q ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi

if ! id "$user" > /dev/null 2>&1; then
    echo "==> Creating $user"
    useradd --create-home --shell /bin/bash "$user"
fi
usermod -aG docker "$user"

echo "==> Installing $dir"
install -d -o root -g root -m 755 "$dir"
install -o root -g root -m 644 "$src/../compose.yaml" "$dir/compose.yaml"
install -o root -g root -m 755 "$src/deploy.sh" "$dir/deploy.sh"
install -o root -g root -m 755 "$src/backup.sh" "$dir/backup.sh"
install -d -o "$user" -g "$user" -m 755 "$dir/state"
# The container runs as uid 10001 (see Dockerfile).
install -d -o 10001 -g 10001 -m 750 "$dir/data"

if [ ! -f "$dir/.env" ]; then
    read -rsp "Gemini API key: " gemini_key
    echo
    umask 027
    printf 'GEMINI_API_KEY=%s\n' "$gemini_key" > "$dir/.env"
    umask 022
fi
chown root:"$user" "$dir/.env"
chmod 640 "$dir/.env"

echo "==> Authorizing the CI deploy key"
install -d -o root -g root -m 755 "/home/$user/.ssh"
printf 'command="%s/deploy.sh",restrict %s\n' "$dir" "$(cat "$pubkey_file")" \
    > "/home/$user/.ssh/authorized_keys"
chmod 644 "/home/$user/.ssh/authorized_keys"

echo "==> Installing the nightly backup"
install -o root -g root -m 644 "$src/sjekka-backup.service" "$src/sjekka-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sjekka-backup.timer

allowed="$(sshd -T 2>/dev/null | awk '$1 == "allowusers" { print $2 }')"
if [ -n "$allowed" ] && ! grep -qx "$user" <<< "$allowed"; then
    echo "!! sshd's AllowUsers doesn't include $user, so CI can't log in. Add it there." >&2
fi

echo "Done. Containers:"
docker ps --format '  {{.Names}}  {{.Status}}'
