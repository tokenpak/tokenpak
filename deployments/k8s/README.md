# TokenPak — Kubernetes Deployment

Deploy TokenPak on Kubernetes (k8s). Tested with k3s, minikube, and EKS.

## Prerequisites

- `kubectl` configured with cluster context
- Docker image built and pushed to a registry
- Kubernetes 1.24+

## Quick Deploy

```bash
# 1. Create namespace
kubectl apply -f namespace.yaml

# 2. Create secrets (replace with real keys)
kubectl create secret generic tokenpak-secrets \
  --from-literal=anthropic_api_key=sk-ant-... \
  --from-literal=openai_api_key=sk-... \
  --namespace=tokenpak

# 3. Apply config, storage, and deployment
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 4. Verify
kubectl get pods -n tokenpak
kubectl logs -f deployment/tokenpak -n tokenpak
```

## Update the Image

```bash
kubectl set image deployment/tokenpak tokenpak=tokenpak:v1.1 -n tokenpak
kubectl rollout status deployment/tokenpak -n tokenpak
```

## Access the Proxy

From inside the cluster:
```
http://tokenpak.tokenpak.svc.cluster.local:8766
```

Port-forward for local testing:
```bash
kubectl port-forward service/tokenpak 8766:8766 -n tokenpak
curl http://localhost:8766/health
```

## Scaling

```bash
kubectl scale deployment tokenpak --replicas=3 -n tokenpak
```

## Monitoring

```bash
kubectl get pods -n tokenpak -w            # watch pod status
kubectl describe pod <pod-name> -n tokenpak # debug issues
kubectl top pod -n tokenpak                 # resource usage
```

## Rollback

```bash
kubectl rollout undo deployment/tokenpak -n tokenpak
kubectl rollout history deployment/tokenpak -n tokenpak
```

## File Structure

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates `tokenpak` namespace |
| `configmap.yaml` | Non-secret config (log level, mode) |
| `secret.yaml` | Template for API keys (do not commit with real keys) |
| `pvc.yaml` | Persistent storage for vault index + telemetry |
| `deployment.yaml` | Main proxy deployment (2 replicas, health checks, resource limits) |
| `service.yaml` | ClusterIP service (internal access) |

## Notes

- The deployment runs as non-root user (UID 1000) for security
- Liveness + readiness probes hit `/health` for Kubernetes health management
- Secrets should be managed by a secrets manager (Vault, AWS SM, GCP SM) in production
- For external ingress, uncomment the LoadBalancer service in `service.yaml`
