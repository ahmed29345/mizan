#!/usr/bin/env bash
# Build the image and deploy Mizan to the local kind cluster, in the safe order:
#   1. build + load the image   2. config, secrets, services   3. migrate Job (wait)
#   4. roll out the API (wait until the new pods are READY)
# Usage: k8s/deploy.sh            (cluster: kind create cluster --name mizan)
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=mizan
KUBECTL=(kubectl --context "kind-$CLUSTER" -n mizan)

[[ -f k8s/secrets.env ]] || { echo "Missing k8s/secrets.env: copy k8s/secrets.env.example"; exit 1; }

echo "==> 1. Build image and load it into the cluster"
docker build -q -t mizan:local . >/dev/null
kind load docker-image mizan:local --name "$CLUSTER" >/dev/null

echo "==> 2. Namespace, config, secrets, external services"
kubectl --context "kind-$CLUSTER" apply -f k8s/namespace.yaml >/dev/null
# Everything EXCEPT the API (label selector) - the API must wait for the migration.
kubectl --context "kind-$CLUSTER" apply -k k8s/ -l 'app.kubernetes.io/component notin (api,migrate)'

echo "==> 3. Database migration (Jobs are immutable, so replace the previous run)"
"${KUBECTL[@]}" delete job mizan-migrate --ignore-not-found >/dev/null
kubectl --context "kind-$CLUSTER" apply -k k8s/ -l app.kubernetes.io/component=migrate
"${KUBECTL[@]}" wait --for=condition=complete job/mizan-migrate --timeout=180s

echo "==> 4. API rollout"
kubectl --context "kind-$CLUSTER" apply -k k8s/ -l app.kubernetes.io/component=api
# The tag "mizan:local" never changes, so force new pods to pick up the rebuilt image.
# (Production uses a unique tag per build, e.g. the git commit, and needs no restart.)
"${KUBECTL[@]}" rollout restart deployment/mizan-api >/dev/null
"${KUBECTL[@]}" rollout status deployment/mizan-api --timeout=300s

echo "==> Done. Reach the API with:"
echo "    kubectl -n mizan port-forward svc/mizan-api 8090:80   then  curl http://127.0.0.1:8090/health"
