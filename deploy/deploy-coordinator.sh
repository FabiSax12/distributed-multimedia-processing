#!/usr/bin/env bash
set -euo pipefail
cd /opt/dmp
sudo -u ec2-user -H git fetch origin main
sudo -u ec2-user -H git reset --hard origin/main
sudo -u ec2-user -H /home/ec2-user/.local/bin/uv sync --package coordinator --frozen
cp deploy/coordinator.service /etc/systemd/system/dmp-coordinator.service
systemctl daemon-reload
systemctl restart dmp-coordinator
sleep 3
systemctl is-active dmp-coordinator