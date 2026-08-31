# Kubernetes

Apply the raw manifests with Kustomize:

```bash
cp deploy/kubernetes/secret.yaml.example deploy/kubernetes/secret.yaml
kubectl apply -f deploy/kubernetes/secret.yaml
kubectl apply -k deploy/kubernetes
kubectl -n semantica rollout status deployment/knowledge-explorer
```

Update the placeholder image digest and ingress host before deploying to production. `secret.yaml` is intentionally ignored from the kustomization; keep only `secret.yaml.example` in git.
