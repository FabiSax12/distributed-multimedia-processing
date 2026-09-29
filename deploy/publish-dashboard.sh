#!/usr/bin/env sh
# Publica el dashboard: build de Vite -> bucket del sitio -> invalidación de CloudFront.
#
# Uso (desde dashboard/):   npm run deploy
# Toma el bucket y la distribución de `terraform output` en infra/; también se
# pueden pasar a mano: SITE_BUCKET=dmp-dev-site DISTRIBUTION_ID=E31AMCS844O3G npm run deploy
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if [ -z "${SITE_BUCKET:-}" ]; then
  SITE_BUCKET="$(terraform -chdir="$ROOT/infra" output -json bucket_names | sed -n 's/.*"site": *"\([^"]*\)".*/\1/p')"
fi
if [ -z "${DISTRIBUTION_ID:-}" ]; then
  DISTRIBUTION_ID="$(terraform -chdir="$ROOT/infra" output -raw distribution_id)"
fi

echo "Bucket: $SITE_BUCKET · Distribución: $DISTRIBUTION_ID"

cd "$ROOT/dashboard"
npm run build

# Assets con hash en el nombre: caché larga. index.html: siempre fresco.
aws s3 sync dist "s3://$SITE_BUCKET" --delete --exclude index.html \
  --cache-control "public,max-age=31536000,immutable"
aws s3 cp dist/index.html "s3://$SITE_BUCKET/index.html" \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"

aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/index.html" "/" >/dev/null
echo "Publicado. El panel queda en la URL de CloudFront (terraform output cloudfront_url)."
