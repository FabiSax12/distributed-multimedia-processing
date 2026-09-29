#!/usr/bin/env bash
# Despliega o actualiza el worker en esta instancia. Se corre como root en cada
# instancia worker, igual que deploy-coordinator.sh. El pool sale de
# /etc/dmp.env, que ya escribe el user_data de Terraform.
set -euo pipefail

# La instancia de metadatos se creó sin ffmpeg. Instalarlo acá evita tener que
# recrearla con Terraform.
if [ ! -x /usr/local/bin/ffmpeg ]; then
  curl -LsS https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz
  mkdir -p /opt/ffmpeg
  tar -xf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1
  ln -sf /opt/ffmpeg/ffmpeg /usr/local/bin/ffmpeg
  ln -sf /opt/ffmpeg/ffprobe /usr/local/bin/ffprobe
  rm -f /tmp/ffmpeg.tar.xz
fi

# Deja terminar la sub-tarea en curso antes de borrar el código.
systemctl stop dmp-worker 2>/dev/null || true
rm -rf /opt/dmp
git clone https://github.com/FabiSax12/distributed-multimedia-processing /opt/dmp
chown -R ec2-user:ec2-user /opt/dmp
cd /opt/dmp
sudo -u ec2-user -H /usr/local/bin/uv sync --package workers --frozen
cp deploy/worker.service /etc/systemd/system/dmp-worker.service
systemctl daemon-reload
systemctl enable --now dmp-worker
sleep 3
systemctl is-active dmp-worker
