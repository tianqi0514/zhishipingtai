# Helm

Deploy the Knowledge Explorer chart:

```bash
helm lint deploy/helm/knowledge-explorer
helm upgrade --install knowledge-explorer deploy/helm/knowledge-explorer --namespace semantica --create-namespace
helm upgrade --install knowledge-explorer deploy/helm/knowledge-explorer --namespace semantica --create-namespace -f deploy/helm/knowledge-explorer/values.prod.yaml
```
