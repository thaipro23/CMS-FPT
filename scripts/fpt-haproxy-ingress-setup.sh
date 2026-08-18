#!/usr/bin/env bash
set -euo pipefail

command -v tutor >/dev/null 2>&1 || {
    echo "Tutor is not available in PATH" >&2
    exit 1
}

command -v python >/dev/null 2>&1 || {
    echo "python is required" >&2
    exit 1
}

REPO_ROOT="$(git rev-parse --show-toplevel)"
PLUGIN_ROOT="$(tutor plugins printroot)"

SRC="$REPO_ROOT/tutor-plugins/fpt_haproxy_ingress.py"
DST="$PLUGIN_ROOT/fpt_haproxy_ingress.py"

INGRESS_CLASS="${FPT_HAPROXY_INGRESS_CLASS:-haproxy}"
CLUSTER_ISSUER="${FPT_HAPROXY_CLUSTER_ISSUER:-letsencrypt-prod}"
TLS_SECRET="${FPT_HAPROXY_TLS_SECRET:-openedx-web-tls}"

MINIO_PUBLIC_HOST="${FPT_MINIO_PUBLIC_HOST:-s3.fpl.edu.vn}"
MINIO_ORIGIN_IP="${FPT_MINIO_ORIGIN_IP:-10.205.194.48}"
MINIO_ORIGIN_PORT="${FPT_MINIO_ORIGIN_PORT:-443}"

[ -f "$SRC" ] || {
    echo "Missing plugin: $SRC" >&2
    exit 1
}

python -m py_compile "$SRC"

mkdir -p "$PLUGIN_ROOT"
rm -f "$DST"
ln -s "$SRC" "$DST"

tutor plugins enable fpt_haproxy_ingress >/dev/null

tutor config save \
  --set ENABLE_WEB_PROXY=false \
  --set ENABLE_HTTPS=true \
  --set FPT_HAPROXY_INGRESS_ENABLED=true \
  --set FPT_HAPROXY_INGRESS_CLASS="$INGRESS_CLASS" \
  --set FPT_HAPROXY_CLUSTER_ISSUER="$CLUSTER_ISSUER" \
  --set FPT_HAPROXY_TLS_SECRET="$TLS_SECRET" \
  --set FPT_MINIO_PUBLIC_HOST="$MINIO_PUBLIC_HOST" \
  --set FPT_MINIO_ORIGIN_IP="$MINIO_ORIGIN_IP" \
  --set FPT_MINIO_ORIGIN_PORT="$MINIO_ORIGIN_PORT"

echo "[fpt-haproxy] ENABLE_WEB_PROXY=false"
echo "[fpt-haproxy] ENABLE_HTTPS=true"
echo "[fpt-haproxy] ingress class=$INGRESS_CLASS"
echo "[fpt-haproxy] cluster issuer=$CLUSTER_ISSUER"
echo "[fpt-haproxy] tls secret=$TLS_SECRET"
echo "[fpt-haproxy] minio public host=$MINIO_PUBLIC_HOST"
echo "[fpt-haproxy] minio origin=$MINIO_ORIGIN_IP:$MINIO_ORIGIN_PORT"
echo "[fpt-haproxy] minio TLS mode=PASSTHROUGH"
