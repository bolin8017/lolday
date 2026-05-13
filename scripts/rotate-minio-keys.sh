#!/usr/bin/env bash
# rotate-minio-keys.sh — generate fresh MinIO svcacct AK/SK for mlflow / harbor / loki
# consumers, update the matching K8s Secrets, roll the Deployments.
#
# Required env (sourced from .lolday-secrets.env or shell):
#   MINIO_ROOT_USER, MINIO_ROOT_PASSWORD  (MinIO root creds for mc admin)
#
# Usage:
#   bash scripts/rotate-minio-keys.sh         # rotate all three
#   bash scripts/rotate-minio-keys.sh mlflow  # rotate one app only
#
# Spec: docs/superpowers/specs/2026-05-12-security-hardening-design.md §6.3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
[ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
if [ -f "$SECRETS" ]; then
  # shellcheck disable=SC1090
  source "$SECRETS"
fi
: "${MINIO_ROOT_USER:?MINIO_ROOT_USER required (MinIO root username from minio Helm release)}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD required (MinIO root password)}"

if [ "$#" -gt 0 ]; then
  APPS=("$@")
else
  APPS=(mlflow harbor loki)
fi

# ---------- helpers ----------

_gen_key() {
  # 40 chars from the alphanum-after-base64 charset. matches the init-job's
  # `tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 40` distribution but uses
  # openssl rand for a deterministic entropy source.
  openssl rand -base64 30 | tr -d '/+=' | head -c 40
}

_secret_name() {
  case "$1" in
    mlflow) echo "mlflow-s3" ;;
    harbor) echo "registry-s3" ;;
    loki) echo "loki-s3" ;;
    *) echo "unknown-app-$1" ;;
  esac
}

_ak_key() { case "$1" in harbor) echo "REGISTRY_STORAGE_S3_ACCESSKEY" ;; *) echo "access-key" ;; esac; }
_sk_key() { case "$1" in harbor) echo "REGISTRY_STORAGE_S3_SECRETKEY" ;; *) echo "secret-key" ;; esac; }

_consumer_deployment() {
  case "$1" in
    mlflow) echo "deploy/lolday-mlflow" ;;
    harbor) echo "deploy/lolday-harbor-registry" ;;
    loki)   echo "statefulset/loki" ;;
  esac
}

# ---------- port-forward MinIO ----------

echo "[1/3] starting kubectl port-forward to MinIO :9000…"
kubectl -n lolday port-forward svc/lolday-minio 9000:9000 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 3   # give the forward time to bind

mc alias set rot http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" \
  >/dev/null

# ---------- per-app rotation ----------

echo "[2/3] rotating svcaccts…"
for app in "${APPS[@]}"; do
  SECRET=$(_secret_name "$app")
  AK_KEY=$(_ak_key "$app")
  SK_KEY=$(_sk_key "$app")
  DEPLOY=$(_consumer_deployment "$app")

  echo "  --- $app ---"
  NEW_AK=$(_gen_key)
  NEW_SK=$(_gen_key)

  # Stage to a tmp dir for safe kubectl input.
  TMP=$(mktemp -d); chmod 700 "$TMP"
  printf '%s' "$NEW_AK" > "$TMP/ak"
  printf '%s' "$NEW_SK" > "$TMP/sk"
  chmod 600 "$TMP/ak" "$TMP/sk"

  # Find the old AK from the existing Secret so we can revoke it after rollout.
  OLD_AK=$(kubectl -n lolday get secret "$SECRET" -o jsonpath="{.data.${AK_KEY}}" \
    | base64 -d 2>/dev/null || echo "")

  # 2a. Create the new svcacct in MinIO.
  mc admin user svcacct add rot "$MINIO_ROOT_USER" \
    --access-key "$NEW_AK" --secret-key "$NEW_SK" \
    --policy "${app}-rw"

  # 2b. Replace the K8s Secret (dry-run | apply pattern, same as deploy.sh).
  kubectl -n lolday create secret generic "$SECRET" \
    --from-file="${AK_KEY}=$TMP/ak" --from-file="${SK_KEY}=$TMP/sk" \
    --dry-run=client -o yaml | kubectl apply -f -

  # 2c. Roll the consumer to pick up the new env.
  kubectl -n lolday rollout restart "$DEPLOY"
  kubectl -n lolday rollout status "$DEPLOY" --timeout=5m

  # 2d. Revoke the OLD svcacct now that the consumer is using NEW.
  if [ -n "$OLD_AK" ] && [ "$OLD_AK" != "$NEW_AK" ]; then
    echo "    revoking OLD AK=${OLD_AK:0:6}…"
    mc admin user svcacct rm rot "$OLD_AK" || true   # already-deleted is OK
  fi

  shred -u "$TMP/ak" "$TMP/sk"; rmdir "$TMP"
done

echo "[3/3] done."
