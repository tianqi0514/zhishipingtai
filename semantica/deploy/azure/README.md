# Azure Container Apps

Deploy with Azure Developer CLI from this template directory:

```bash
cd deploy/azure
azd auth login
azd init --environment semantica-ke
azd env set AZURE_LOCATION eastus

# The template defaults to an internal (private) Container Apps environment.
# Provide the resource ID of an existing subnet (delegated to Microsoft.App/environments):
azd env set AZURE_INFRASTRUCTURE_SUBNET_ID /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>

# For a quick public dev/test deployment without a VNet, override the default:
# azd env set AZURE_INFRASTRUCTURE_SUBNET_ID "" and set vnetInternal=false in main.parameters.json

azd up
```

After the first deploy, set `allowedOrigins` in `main.parameters.json` to the Container App URL printed by `azd up` (e.g. `https://<app-name>.<unique>.eastus.azurecontainerapps.io`), then re-run `azd up` to apply the CORS restriction.

The Bicep template provisions:

- A Container Apps managed environment with an internal load balancer (private VNet, no public IP) and a system-assigned managed identity on the Container App (AZR-000363 / AZR-000361 compliant).
- HTTP ingress, scale-to-zero, max 10 replicas, and a `/api/health` liveness probe.
