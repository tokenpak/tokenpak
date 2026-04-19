# TokenPak — GCP Cloud Run

Deploy TokenPak on Google Cloud Run (fully managed serverless containers).

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- Project selected (`gcloud config set project YOUR_PROJECT_ID`)
- Artifact Registry or Container Registry enabled
- API keys stored in Secret Manager

## Setup Steps

### 1. Push Image to Artifact Registry

```bash
# Configure Docker auth
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build and push
docker build -t tokenpak .
docker tag tokenpak:latest \
  us-central1-docker.pkg.dev/YOUR_PROJECT_ID/tokenpak/proxy:latest
docker push us-central1-docker.pkg.dev/YOUR_PROJECT_ID/tokenpak/proxy:latest
```

### 2. Store API Keys in Secret Manager

```bash
# Create secrets
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
echo -n "sk-..." | gcloud secrets create openai-api-key --data-file=-

# Grant Cloud Run access to secrets
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:YOUR_PROJECT_ID-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 3. Deploy to Cloud Run

```bash
gcloud run deploy tokenpak \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/tokenpak/proxy:latest \
  --platform managed \
  --region us-central1 \
  --port 8766 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 5 \
  --set-env-vars TOKENPAK_PORT=8766,TOKENPAK_MODE=hybrid,TOKENPAK_LOG_LEVEL=info \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest \
  --no-allow-unauthenticated  # remove to allow public access
```

### 4. Verify Deployment

```bash
# Get the service URL
SERVICE_URL=$(gcloud run services describe tokenpak \
  --region us-central1 \
  --format='value(status.url)')

# Health check (will need auth if --no-allow-unauthenticated was set)
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "$SERVICE_URL/health"
```

## Update Image

```bash
# After pushing updated image:
gcloud run services update tokenpak \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/tokenpak/proxy:v1.1 \
  --region us-central1
```

## View Logs

```bash
gcloud logs tail --service=tokenpak --region=us-central1
# Or in Cloud Console: Logging → Logs Explorer → resource.type="cloud_run_revision"
```

## Notes

- Cloud Run scales to zero when idle (cost-efficient for low-traffic)
- Set `--min-instances 1` to avoid cold starts for time-sensitive use cases
- API keys are injected via Secret Manager at runtime (never baked into image)
- Use `--allow-unauthenticated` for internal VPC deployments with IAP
- Replace `YOUR_PROJECT_ID` throughout with your GCP project ID
