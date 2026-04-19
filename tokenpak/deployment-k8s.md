# Deployment: Kubernetes

Deploy TokenPak on Kubernetes (k3s, minikube, EKS, GKE, AKS).

## Quick Deploy

```bash
cd deployments/k8s

# 1. Create namespace
kubectl apply -f namespace.yaml

# 2. Create secrets (replace keys with real values)
kubectl create secret generic tokenpak-secrets \
  --from-literal=anthropic_api_key=sk-ant-... \
  --from-literal=openai_api_key=sk-... \
  --namespace=tokenpak

# 3. Apply remaining resources
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 4. Port-forward and verify
kubectl port-forward service/tokenpak 8766:8766 -n tokenpak &
curl http://localhost:8766/health
```

## Monitor

```bash
kubectl get pods -n tokenpak -w
kubectl logs -f deployment/tokenpak -n tokenpak
```

## Scale

```bash
kubectl scale deployment tokenpak --replicas=3 -n tokenpak
```

## Rollback

```bash
kubectl rollout undo deployment/tokenpak -n tokenpak
```

## Deployment Files

See `deployments/k8s/` for all YAML manifests.
