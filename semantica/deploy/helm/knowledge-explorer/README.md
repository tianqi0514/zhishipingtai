# Knowledge Explorer Helm Chart

```bash
helm lint deploy/helm/knowledge-explorer
helm upgrade --install knowledge-explorer deploy/helm/knowledge-explorer --namespace semantica --create-namespace
helm upgrade --install knowledge-explorer deploy/helm/knowledge-explorer --namespace semantica --create-namespace -f deploy/helm/knowledge-explorer/values.prod.yaml
```

Set `autoscaling.enabled=true` to render the HPA. Put sensitive values in Kubernetes Secrets and reference them outside this chart, or pass non-secret env values through `env`.
