#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ai-workspace}"
SERVICE=/etc/systemd/system/ai-workspace-disk-guard.service
TIMER=/etc/systemd/system/ai-workspace-disk-guard.timer

[[ "${EUID}" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "${PROJECT_DIR}/scripts/disk_guard.sh" ]] || chmod +x "${PROJECT_DIR}/scripts/disk_guard.sh"

cat >"${SERVICE}" <<EOF
[Unit]
Description=AI Workspace safe Docker/disk cleanup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
Environment=PROJECT_DIR=${PROJECT_DIR}
Environment=MIN_FREE_GB=6
Environment=TARGET_FREE_GB=8
Environment=KEEP_BACKUPS=1
ExecStart=/bin/bash ${PROJECT_DIR}/scripts/disk_guard.sh
Nice=10
IOSchedulingClass=idle
EOF

cat >"${TIMER}" <<'EOF'
[Unit]
Description=Run AI Workspace disk guard regularly

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ai-workspace-disk-guard.timer
systemctl start ai-workspace-disk-guard.service

echo "[PASS] ai-workspace-disk-guard.timer enabled"
systemctl --no-pager --full status ai-workspace-disk-guard.timer || true
