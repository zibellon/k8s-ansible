# linstor / install-operator

Piraeus operator installed from official OCI chart `oci://ghcr.io/piraeusdatastore/piraeus-operator/piraeus`.

Helm install выполняется в фазе `[install-operator]` playbook'a `playbook-app/linstor-install.yaml` (SUB-6).

CRDs включены через `installCRDs: true` (см. `piraeus_operator_helm_values` в `hosts-vars/linstor.yaml`).

**Не chart директория** — нет `Chart.yaml`/`values.yaml`/`templates/`. Pattern совпадает с `playbook-app/charts/longhorn/install/`.
