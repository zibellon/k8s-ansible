# metrics-server install

Installed via official Helm chart: https://artifacthub.io/packages/helm/metrics-server/metrics-server

```bash
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm upgrade --install metrics-server metrics-server/metrics-server --namespace kube-system
```
