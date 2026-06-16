# Что еще не готово

## Обновление
1. Обновление версии Ubuntu (apt dist upgrade ...)
2. Обновление пакетов на серверах (apt update ...)
3. Обновление компонентов, которые ставились через GIT (containerd, runc, cni)
4. Обновление kubelet, kubectl, kubeadm

## Структура установки HELM
Разделить все установки на три шага. Для каждого шага - отдельный helm-chart. Но все три шага в одном playbook
- pre. Вот тут ставим все Network-policy. И мб что-то еще
- install. Вот тут ставим основной компонент. Вот этот helm-chart будет не у всех. Например у cilium его не будет, так как основная установка производится из официального helm.
- post. Вот тут ставим что-то, что ставится только после установки основного компонента (для cilium, в этом шаге ставим CiliumClusterwideNetworkPolicy - можно сделать тол ко после установки crds)

## Что дополнить и доделать
1. Сделать отдельный playbook - для проверки состояния кластера. Все статистики по всему что есть. Как-то так, чтобы можно было его запустить и получить сводку по всем серверам и всем контейнерам и всем компонентам. Что там вообще происходит, все ли работает и так далее
2. Добавить контроль по: убирать taint с control-plane или нет
   1. Типо - хотим запускать что-то на голове или нет. Учесть этот момент в longhorn. Мб на manager будет большой диск, и ему грех простаивать ? Подумать про эту механику. Если там будет taint - то как будут работать остальные компоненты ?
3. Обновить tasks = tasks-set-is-cluster-init.yaml + tasks-set-is-node-joined.yaml
   1. Там надо понимать, была ли инициализация кластера на manager | worker node ?
4.  argocd
    1.  правила безопасности, чтобы нельзя было никому навредить
5.  Добавить установку https://github.com/stakater/Reloader
    1.  перезапуск подов, если configMap | secret - изменились
6.  Есть playbook
    1.  которые запускаются до инициализации
    2.  которые запускаются после инициализации
    3.  которые должны быть строго с лимитами
7.  Linstor
    1.  крона для проверки целостности данных
    2.  точно не добавил. Надо сделать
8.  Gitlab. Для работы с Registry, нужно установить дополнительную Postgres

## Просто мысли по улучшению и изменению репозиторию
1. VAULT
   1. как создать токен для другой role ? policy ?
   2. как токен для root
2. ротация
   1. Vault
      1. основные ключи = уже работает
      2. root_token = как ? неизвестно ... (тоже нужно будет засунуть на все сервера)
      3. admin_token  - аналогичный вопрос ???
   2. Gitlab (полный список)
      1. На все части, которые используют секреты - нужно повесить annotations: reloader.stakater.com/auto: "true"
      2. POSTGRES
         1. сгенерить новый пароль
         2. положить его во временный файл на сервере
         3. выполнить внутри postgres: ALTER USER ...
         4. обновить в VAULT
         5. удадить временный файл
         6. ESO - подхватит и обновит k8s.secret
         7. Reloader - подхватит и перезапустит: postgres + ВСЕ контейнеры от gitlab, которые зависят от него
      3. REDIS
         1. сгенерить новый пароль
         2. положить его во временный файл на сервере
         3. обновить в VAULT
         4. удалить временный файл
         5. ESO - подхватит и обновит k8s.secret
         6. Reloader - подхватит и перезапустит: redis + ВСЕ контейнеры от gitlab, которые зависят от него
      4. MINIO
         1. сгенерить новый пароль
         2. положить его во временный файл на сервере
         3. обновить в VAULT
         4. удалить временный файл
         5. ESO - подхватит и обновит k8s.secret
         6. Reloader - подхватит и перезапустит: mino + ВСЕ контейнеры от gitlab, которые зависят от него
      5. gitlab-users (root, admin, devops)
         1. сгенерить новый пароль
         2. положить его во временный файл на сервере
         3. обновить в VAULT
         4. установить пользователю
         5. обновить PAT (по имени)
         6. положить в vault - именно PAT
         7. удалить временный файл
   3. ArgoCD
      1. admin-user
         1. сгенерить новый пароль
         2. положить его во временный файл на сервере
         3. обновить в VAULT
         4. установить пользователю
         5. положить в vault - именно PAT
         6. удалить временный файл

------

# Что по ansible - на потом

- Обнаружена странная ошибка с сертификатами и sans при работе с metrics-server ?

- argocd-git-ops. Генерация ключей. Отдельный массив argocd_gitops_repo_creds. Проверка на уникальность, проверка что каждый элемент имеет пересечение по vaultpath (из ESO integration).

---

Суффикс _download_host не в §1.1 (Per-component suffix convention). Логично дополнить _download_host + может _download_url — это мини-задача, вне scope этой.

containerd_service_url тянет из main ветки upstream — это отдельная тема стабильности (upstream может сломать юнит). Вне scope, но держу в голове как риск: если когда-нибудь containerd выпустит breaking change в containerd.service на main, — переключимся на локальный template в репо.

------

Одно косметическое
...ignoring в Wait for apt/dpkg locks пугает оператора — будет выглядеть как «что-то сломалось» в логах, хотя это штатный случай «locks свободны». Можно одной правкой убрать этот шум:

# было
ignore_errors: true

# станет
failed_when: false
Разница: failed_when: false вообще не помечает task как failed (Ansible увидит rc=1 как success). until: rc != 0 при этом продолжает работать ровно так же — логика retry не зависит от failed_when. В логе будет просто ok без красного [ERROR].

---

Side issues, не закрытые в этом плане (зафиксированы для отдельной задачи):

playbook-app/tasks/ физически содержит 22 файла; tasks-wait-secret.yaml есть на диске, но не описан в reusable-tasks.md. После текущей чистки counter (21 tasks) совпадает с числом описаний, но не с числом физических файлов. Документирование tasks-wait-secret.yaml + counter (22 tasks) — отдельная задача, в этот план не включалась.

---

некрасивый вывод

TASK [Print final status] *************************************************************************************************************************************************
ok: [k8s-manager-1] => {
    "msg": [
        "=== Kubernetes Components Installed ===",
        "containerd: containerd github.com/containerd/containerd/v2 v2.2.2 301b2dac98f15c27117da5c8af12118a041a31d9",
        "runc: runc version 1.4.2",
        "kubeadm: {\n  \"clientVersion\": {\n    \"major\": \"1\",\n    \"minor\": \"35\",\n    \"gitVersion\": \"v1.35.4\",\n    \"gitCommit\": \"7b8c6cf0edd376b3d7c2f255142977c7f93db258\",\n    \"gitTreeState\": \"clean\",\n    \"buildDate\": \"2026-04-15T18:03:27Z\",\n    \"goVersion\": \"go1.25.9\",\n    \"compiler\": \"gc\",\n    \"platform\": \"linux/amd64\"\n  }\n}",
        "kubelet: Kubernetes v1.35.4",
        "kubectl: {\n  \"clientVersion\": {\n    \"major\": \"1\",\n    \"minor\": \"35\",\n    \"gitVersion\": \"v1.35.4\",\n    \"gitCommit\": \"7b8c6cf0edd376b3d7c2f255142977c7f93db258\",\n    \"gitTreeState\": \"clean\",\n    \"buildDate\": \"2026-04-15T18:04:08Z\",\n    \"goVersion\": \"go1.25.9\",\n    \"compiler\": \"gc\",\n    \"platform\": \"linux/amd64\"\n  },\n  \"kustomizeVersion\": \"v5.7.1\"\n}"
    ]
}
ok: [k8s-worker-1] => {
    "msg": [
        "=== Kubernetes Components Installed ===",
        "containerd: containerd github.com/containerd/containerd/v2 v2.2.2 301b2dac98f15c27117da5c8af12118a041a31d9",
        "runc: runc version 1.4.2",
        "kubeadm: {\n  \"clientVersion\": {\n    \"major\": \"1\",\n    \"minor\": \"35\",\n    \"gitVersion\": \"v1.35.4\",\n    \"gitCommit\": \"7b8c6cf0edd376b3d7c2f255142977c7f93db258\",\n    \"gitTreeState\": \"clean\",\n    \"buildDate\": \"2026-04-15T18:03:27Z\",\n    \"goVersion\": \"go1.25.9\",\n    \"compiler\": \"gc\",\n    \"platform\": \"linux/amd64\"\n  }\n}",
        "kubelet: Kubernetes v1.35.4",
        "kubectl: {\n  \"clientVersion\": {\n    \"major\": \"1\",\n    \"minor\": \"35\",\n    \"gitVersion\": \"v1.35.4\",\n    \"gitCommit\": \"7b8c6cf0edd376b3d7c2f255142977c7f93db258\",\n    \"gitTreeState\": \"clean\",\n    \"buildDate\": \"2026-04-15T18:04:08Z\",\n    \"goVersion\": \"go1.25.9\",\n    \"compiler\": \"gc\",\n    \"platform\": \"linux/amd64\"\n  },\n  \"kustomizeVersion\": \"v5.7.1\"\n}"
    ]
}

---

некрасивый вывод

TASK [Print k9s version] **************************************************************************************************************************************************
ok: [k8s-manager-1] => {
    "msg": "k9s version: \u001b[36m ____  __ ________       \u001b[0m\n\u001b[36m|    |/  /   __   \\______\u001b[0m\n\u001b[36m|       /\\____    /  ___/\u001b[0m\n\u001b[36m|    \\   \\  /    /\\___  \\\u001b[0m\n\u001b[36m|____|\\__ \\/____//____  /\u001b[0m\n\u001b[36m         \\/           \\/ \u001b[0m\n\n\u001b[36mVersion:\u001b[0m    v0.50.18\n\u001b[36mCommit:\u001b[0m     6dbf571c59fd48dc5b384aa46ee7f3e5decfae2b\n\u001b[36mDate:\u001b[0m       2026-01-11T20:09:14Z"
}

TASK [Print k9s info] *****************************************************************************************************************************************************
ok: [k8s-manager-1] => {
    "msg": "k9s info: \u001b[36m ____  __ ________       \u001b[0m\n\u001b[36m|    |/  /   __   \\______\u001b[0m\n\u001b[36m|       /\\____    /  ___/\u001b[0m\n\u001b[36m|    \\   \\  /    /\\___  \\\u001b[0m\n\u001b[36m|____|\\__ \\/____//____  /\u001b[0m\n\u001b[36m         \\/           \\/ \u001b[0m\n\n\u001b[36mVersion:\u001b[0m           v0.50.18\n\u001b[36mConfig:\u001b[0m            /root/.config/k9s/config.yaml\n\u001b[36mCustom Views:\u001b[0m      /root/.config/k9s/views.yaml\n\u001b[36mPlugins:\u001b[0m           /root/.config/k9s/plugins.yaml\n\u001b[36mHotkeys:\u001b[0m           /root/.config/k9s/hotkeys.yaml\n\u001b[36mAliases:\u001b[0m           /root/.config/k9s/aliases.yaml\n\u001b[36mSkins:\u001b[0m             /root/.config/k9s/skins\n\u001b[36mContext Configs:\u001b[0m   /root/.local/share/k9s/clusters\n\u001b[36mLogs:\u001b[0m              /root/.local/state/k9s/k9s.log\n\u001b[36mBenchmarks:\u001b[0m        /root/.local/state/k9s/benchmarks\n\u001b[36mScreenDumps:\u001b[0m       /root/.local/state/k9s/screen-dumps"
}

---

некрасивый вывод

TASK [Print Helm version] *************************************************************************************************************************************************
ok: [k8s-manager-1] => {
    "msg": "Helm: version.BuildInfo{Version:\"v3.20.2\", GitCommit:\"8fb76d6ab555577e98e23b7500009537a471feee\", GitTreeState:\"clean\", GoVersion:\"go1.25.9\"}"
}

------

вынести в отдельный таск

    - name: "[loki-verify] Verify NetworkPolicies"
      command: kubectl get networkpolicies -n {{ loki_namespace }}
      register: np_verify
      changed_when: false
      delegate_to: "{{ master_manager_fact }}"
      run_once: true
      tags: [always]

    - name: "[loki-verify] Show NetworkPolicies"
      debug:
        var: np_verify.stdout_lines
      delegate_to: "{{ master_manager_fact }}"
      run_once: true
      tags: [always]

------

prometheus-operator - не расширил диск для Prometheus и Alertmanager

------

## Известные ограничения

### NetworkPolicy ports refactor

- `playbook-app/charts/argocd/install/templates/argocd.yaml` — это vendored upstream ArgoCD chart с встроенными NP, в которых порты захардкожены (8080, 8082-8084, 5556-5558, 9001, 6379, 5557, 7000 и др.). При общем рефакторинге NP-портов в `values.yaml` (см. `reference/networking.md` §7) этот файл намеренно пропущен — модификация upstream-чарта сломала бы синхронизацию с upstream при следующем обновлении ArgoCD. Отдельная задача (если будет): fork-aware retrofit либо ожидание upstream-параметризации этих портов.
- Перенос новых port-ключей из chart-овых `values.yaml` в `hosts-vars/<c>.yaml` (чтобы operator мог override per environment) — отдельная задача, в текущем рефакторинге не сделана.

------

учимся работать с LVM

1. fdisk -l
2. cfdisk
   1. красивый interactive UI
3. 

------

## LINSTOR / Piraeus stack — отложенные задачи

- **Storage pools на LVM thinpool** — при появлении baremetal с extra disks переопределить `linstor_cluster_helm_values.linstorSatelliteConfigurations[*].storagePools` с `fileThinPool` на `lvmThinPool` + `source.hostDevices: [/dev/sdb]` (auto-pvcreate через operator). Имена pool'ов (`linstor-managers`/`linstor-workers`) остаются — SC переопределять не нужно. См. plan §"Out of scope" в `.claude/plans/teamlead-nested-volcano.md`.
- **`LinstorNodeConnection` CR'ы для multi-NIC / cross-region** — `linstor_cluster_helm_values.linstorNodeConnections: []` сейчас пустой. Активировать когда понадобится DRBD replication через отдельный VLAN (`paths`) или разные DRBD protocols per node pair (`properties` с `DrbdOptions/Net/protocol: A` для cross-region).
- **`monitoring.enabled: true` cross-namespace ingress** в `charts/linstor/pre/templates/network-policy.yaml` — когда `linstor_cluster_helm_values.monitoring.enabled: true` (уже так), Prometheus из `mon-system` должен иметь ingress к ServiceMonitor target'ам. Сейчас Prometheus scraping работает через intra-cluster pod IP routing — но если активируется strict NetworkPolicy enforcement, нужен explicit ingress rule.

------

## Migration notes — kustomize refactor (commits bae0aa8..0395f0b)

**SUB-0a** (`tasks-helm-template-kustomize-build.yaml` создан): все LOCAL-managed chart phase'ы теперь используют unified flow — `helm template` → staging `-k-tmp/` → `kubectl kustomize` → output `-k/` → `helm install` из `-k/`. Старый `tasks-kustomize-build.yaml` удалён.

**SUB-0a-fix** (commit 7e0c6d4): kustomize `namespace:` transformer удалён из flow — он ломал multi-namespace charts (переписывал `metadata.namespace` у ресурсов с явно заданным namespace). Namespace теперь подаётся исключительно через `helm template --namespace`.

**Переменная `argocd_kustomize_patches`** переименована в `argocd_install_kustomize_patches` (SUB-14). Companion `argocd_kustomize_patches_extra` удалён — оператор-side override заменяет base целиком (нет concat-semantics).

**`mon_system_prometheus_operator_kustomize_patches_extra`** удалён (SUB-0b). Оператор-side override заменяет base целиком.

**Extension point для всех компонентов**: `<c>_<phase>_kustomize_patches: []` (default пустой список = zero diff на первом deploy). NO `_extra` companion — оператор заменяет base целиком через `hosts-vars-override/`.

------

## SeaweedFS — отложенные задачи

- **Backup to remote S3** — после появления реальных production-данных активировать backup из SeaweedFS в external S3 (Wasabi / Backblaze B2). Mechanism: Deployment с `filer.remote.sync` daemon (continuous async one-way replication) → remote bucket с versioning enabled. Опционально дополнительно — `weed backup` CronJob (snapshot-style раз в сутки). Creds для remote S3 хранятся в Vault path `eso-secret/seaweedfs/backup/remote-s3-creds` (operator делает через одноразовый `tasks-vault-put`). Cost estimate: ~$60/мес Wasabi за 10 TB зеркала. На тестовом контуре пока не нужно — нет реальных данных для защиты.
- **Per-project IAM bootstrap automation** — текущая итерация SeaweedFS plan (F1-F4) реализовала только базовый install. Per-project IAM creation (создание project user, N buckets, quotas, K8s Secret через ESO) пока — manual operator workflow через `kubectl -n seaweedfs exec deploy/seaweedfs-s3 -- weed shell` → `s3.configure ...`. Будущая mini-task F5: reusable `tasks-seaweedfs-project-create.yaml` (input: project_name + buckets_list + quotas) → автоматизирует full flow.


интересный моммент - про перенос docker-image

1. На машине с доступом в интернет: docker pull, docker save, и упаковывают в .tar.
2. Переносят .tar в закрытый контур.
3. На локальной машине: docker load, docker tag (на адрес 1.2.3.4:15001) и docker push.