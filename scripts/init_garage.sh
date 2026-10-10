#!/usr/bin/env bash
set -euo pipefail

# 🛰️ Garage S3 初期化 & セットアップスクリプト
#
# 本スクリプトは Garage の Secret 生成、クラスタレイアウトの初期化、
# バケット作成、およびアクセスキーの発行を自動化します。

NAMESPACE="storage"
BUCKET_NAME="satellite-recordings"
KEY_NAME="tracker-key"

echo "=== 1. Garage Secret の検証 / 生成 ==="
if ! kubectl get secret garage-secret -n "${NAMESPACE}" &>/dev/null; then
    echo "garage-secret が存在しないため、ランダム生成します..."
    RPC_SECRET=$(openssl rand -hex 32)
    ADMIN_TOKEN=$(openssl rand -hex 32)

    kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic garage-secret \
        --namespace="${NAMESPACE}" \
        --from-literal=rpc_secret="${RPC_SECRET}" \
        --from-literal=admin_token="${ADMIN_TOKEN}"
    echo "garage-secret を作成しました。"
else
    echo "garage-secret は既に存在します。"
fi

echo "=== 2. Garage Pod の稼働待機 ==="
kubectl apply -k infrastructure/storage/garage/
echo "garage-0 Pod の Ready を待機中..."
kubectl rollout status statefulset/garage -n "${NAMESPACE}" --timeout=120s

echo "=== 3. Garage クラスタレイアウトの初期化 ==="
# ノードIDの取得
NODE_ID=$(kubectl exec -n "${NAMESPACE}" garage-0 -- /garage node id | head -n 1 | awk '{print $1}')
echo "Detected Garage Node ID: ${NODE_ID}"

# レイアウトの割り当てと適用 (single-node: 10GB 容量指定)
kubectl exec -n "${NAMESPACE}" garage-0 -- /garage layout assign -z dc1 -c 10G "${NODE_ID}" || true
CURRENT_VERSION=$(kubectl exec -n "${NAMESPACE}" garage-0 -- /garage layout show | grep "Role changes" -A 1 | tail -n 1 | awk '{print $1}' || echo "1")
if [[ "${CURRENT_VERSION}" =~ ^[0-9]+$ ]]; then
    kubectl exec -n "${NAMESPACE}" garage-0 -- /garage layout apply --version "${CURRENT_VERSION}" || true
else
    kubectl exec -n "${NAMESPACE}" garage-0 -- /garage layout apply --version 1 || true
fi

echo "=== 4. バケット作成 ==="
if ! kubectl exec -n "${NAMESPACE}" garage-0 -- /garage bucket list | grep -q "${BUCKET_NAME}"; then
    kubectl exec -n "${NAMESPACE}" garage-0 -- /garage bucket create "${BUCKET_NAME}"
    echo "バケット ${BUCKET_NAME} を作成しました。"
else
    echo "バケット ${BUCKET_NAME} は既に存在します。"
fi

echo "=== 5. API アクセスキーの作成と権限付与 ==="
if ! kubectl exec -n "${NAMESPACE}" garage-0 -- /garage key list | grep -q "${KEY_NAME}"; then
    KEY_INFO=$(kubectl exec -n "${NAMESPACE}" garage-0 -- /garage key create "${KEY_NAME}")
    echo "${KEY_INFO}"
    kubectl exec -n "${NAMESPACE}" garage-0 -- /garage bucket allow "${BUCKET_NAME}" --key "${KEY_NAME}" --read --write
    echo "キー ${KEY_NAME} にバケット ${BUCKET_NAME} への Read/Write 権限を付与しました。"
else
    echo "キー ${KEY_NAME} は既に存在します。"
fi

echo "=== Garage S3 初期化完了 ==="
echo "S3 Endpoint: http://garage-s3.storage.svc.cluster.local:3900"
