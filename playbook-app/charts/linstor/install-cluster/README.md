# linstor / install-cluster

Piraeus Datastore (`LinstorCluster` + `LinstorSatelliteConfiguration` + TLS + monitoring + StorageClass'ы) installed from official OCI chart `oci://ghcr.io/piraeusdatastore/helm-charts/linstor-cluster`.

Helm install выполняется в фазе `[install-cluster]` playbook'a `playbook-app/linstor-install.yaml` (SUB-6).

Все CR'ы (`LinstorCluster`, `LinstorSatelliteConfiguration`, `LinstorNodeConnection`) + Prometheus monitoring + StorageClasses (9 SC, 3 tier × 3 modes) конфигурируются через `linstor_cluster_helm_values` в `hosts-vars/linstor.yaml`.

**Не chart директория** — нет `Chart.yaml`/`values.yaml`/`templates/`. Pattern совпадает с `playbook-app/charts/longhorn/install/`.
