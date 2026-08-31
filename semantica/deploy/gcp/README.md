# GCP Cloud Run

Create the Secret Manager entries, then submit the Cloud Build pipeline:

```bash
gcloud services enable cloudbuild.googleapis.com run.googleapis.com secretmanager.googleapis.com
printf "falkordb-host.example.internal" | gcloud secrets create falkordb-host --data-file=-
printf "6379" | gcloud secrets create falkordb-port --data-file=-
# Set _ALLOWED_ORIGINS to your actual service URL after the first deploy.
gcloud builds submit --config deploy/gcp/cloudbuild.yaml \
  --substitutions _REGION=us-central1,_SERVICE_NAME=knowledge-explorer,_ALLOWED_ORIGINS=https://knowledge-explorer-REPLACE_ME.a.run.app
```

For declarative deploys, substitute your project ID and deploy in one step:

```bash
sed "s/PROJECT_ID/$(gcloud config get-value project)/g" deploy/gcp/cloudrun-service.yaml | \
  gcloud run services replace - --region us-central1
```
