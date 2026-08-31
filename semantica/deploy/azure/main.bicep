targetScope = 'resourceGroup'

param environmentName string = 'semantica-ke'
param location string = resourceGroup().location
param imageName string
param containerPort int = 8000
param allowedOrigins string = '*'
param falkordbHost string = 'falkordb'
param falkordbPort string = '6379'

@description('Deploy the managed environment with an internal load balancer (no public IP). Recommended for production. Set false only for quick dev/test deployments.')
param vnetInternal bool = true

@description('Resource ID of an existing subnet for the Container Apps environment. Required when vnetInternal is true. E.g. /subscriptions/.../subnets/aca-subnet')
param infrastructureSubnetId string = ''

var appName = '${environmentName}-explorer'
var logAnalyticsName = '${environmentName}-logs'
var managedEnvironmentName = '${environmentName}-env'

// Two concrete objects avoids a `null` ternary branch, which crashes checkov's Bicep parser.
var vnetConfigInternal = {
  internal: true
  infrastructureSubnetId: infrastructureSubnetId
}
var vnetConfigExternal = {
  internal: false
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: managedEnvironmentName
  location: location
  properties: {
    vnetConfiguration: vnetInternal ? vnetConfigInternal : vnetConfigExternal
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: {
    'azd-service-name': 'explorer'
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: containerPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'explorer'
          image: imageName
          env: [
            {
              name: 'ALLOWED_ORIGINS'
              value: allowedOrigins
            }
            {
              name: 'FALKORDB_HOST'
              value: falkordbHost
            }
            {
              name: 'FALKORDB_PORT'
              value: falkordbPort
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: containerPort
              }
              initialDelaySeconds: 20
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 10
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

output endpoint string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
