#!/usr/bin/env bash
# Sube el dataset al bucket: 30 copias de los 16 archivos base (lote-01 a lote-30) y el manifiesto.
# Uso:  bash dataset/subir-dataset.sh <carpeta con los archivos base> [bucket]
set -euo pipefail

SRC="${1:?falta la carpeta con los archivos base}"
BUCKET="${2:-dmp-dev-dataset}"
HERE="$(cd "$(dirname "$0")" && pwd)"

for i in $(seq -w 1 30); do
  aws s3 cp "$SRC" "s3://${BUCKET}/lote-${i}/" --recursive --exclude "manifest.json" --exclude "*.sh"
done
aws s3 cp "$HERE/manifest.json" "s3://${BUCKET}/manifest.json"
