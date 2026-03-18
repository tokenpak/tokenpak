# Deployment: GCP Cloud Run

Deploy TokenPak on Google Cloud Run (serverless, scales to zero).

## Quick Reference

```bash
# 1. Push image to Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build -t tokenpak .
docker tag tokenpak us-central1-docker.pkg.dev/PROJECT_ID/tokenpak/proxy:latest
docker push us-central1-docker.pkg.dev/PROJECT_ID/tokenpak/proxy:latest

# 2. Store API keys in Secret Manager
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-

# 3. Deploy
gcloud run deploy tokenpak \
  --image us-central1-docker.pkg.dev/PROJECT_ID/tokenpak/proxy:latest \
  --platform managed \
  --region us-central1 \
  --port 8766 \
  --memory 512Mi \
  --set-env-vars TOKENPAK_PORT=8766,TOKENPAK_MODE=hybrid \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --no-allow-unauthenticated
```

## Verify

```bash
SERVICE_URL=$(gcloud run services describe tokenpak --region us-central1 --format='value(status.url)')
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$SERVICE_URL/health"
```

## View Logs

```bash
gcloud logs tail --service=tokenpak --region=us-central1
```

## Deployment Files

See `deployments/gcp-cloud-run/README.md` for the full deployment guide.
