# PaaS-платформа на кластере 1520-tech — дизайн

Проектная документация облачной платформы для клиентов поверх существующего bare-metal Kubernetes-кластера, который автоматизирует этот репозиторий. Статус: **черновик дизайна**, 2026-09-11. Код не написан; в репозитории ничего, кроме этой директории, не менялось.

## Как читать

| Если есть… | Читать |
|---|---|
| 5 минут | [00-executive-summary.md](00-executive-summary.md) |
| 30 минут | 00 → [01-architecture-overview.md](01-architecture-overview.md) → [18-risks-and-owner-decisions.md](18-risks-and-owner-decisions.md) → [17-roadmap.md](17-roadmap.md) |
| перед юристом | [16-legal-ru.md](16-legal-ru.md) |
| перед написанием кода | [19-requirements.md](19-requirements.md) → 02 … 15 по порядку |

## Карта документов

| Файл | О чём |
|---|---|
| [00-executive-summary.md](00-executive-summary.md) | Вердикт, что изменено относительно исходной идеи, топ-решения владельца |
| [01-architecture-overview.md](01-architecture-overview.md) | Граница Ansible ↔ PaaS, компоненты, главная схема, потоки, слои безопасности, egress, размещение по нодам, новые ansible-компоненты, отказы, индекс решений D1-D15, non-goals |
| [02-tenancy-and-isolation.md](02-tenancy-and-isolation.md) | Organization → Project → namespace, жизненный цикл namespace, tenant pool, user namespaces, сетевая изоляция, квоты |
| [03-security-model.md](03-security-model.md) | Модель угроз, `Harden()`, ValidatingAdmissionPolicy, разделение прав backend, сценарии компрометации, аудит, абьюз, security-чеклист |
| [04-control-plane-go.md](04-control-plane-go.md) | Go-backend: модульный монолит, три бинаря, API, auth, очередь River, state machines, адаптеры, тестирование |
| [05-data-model.md](05-data-model.md) | Полная схема Postgres control plane, ER-диаграмма |
| [06-delivery-pipeline.md](06-delivery-pipeline.md) | backend → GitLab → tenant-ArgoCD + provisioner; раскладка репо, AppProject/Application, откат, удаление, масштаб |
| [07-svc-compute.md](07-svc-compute.md) | Контейнеры: модель App, размеры, health-checks, тома, перевод ошибок k8s |
| [08-svc-ingress-domains-ip.md](08-svc-ingress-domains-ip.md) | Путь запроса, поддомены, custom domains, сертификаты на масштабе, Cloudflare-режим, L4, IP-слоты |
| [09-svc-databases.md](09-svc-databases.md) | CloudNativePG, Valkey, общий NATS с accounts, backup/PITR, лицензионная карта |
| [10-svc-registry-harbor.md](10-svc-registry-harbor.md) | Harbor: project на организацию, robot-аккаунты, proxy-cache, квоты, хранилище |
| [11-svc-object-storage-s3.md](11-svc-object-storage-s3.md) | S3 на SeaweedFS: identity, матрица совместимости клиентов, квоты, отдельный инстанс |
| [12-svc-secrets.md](12-svc-secrets.md) | «Секреты как фича» поверх Vault + ESO, templated policy, поток секрета |
| [13-billing-and-quotas.md](13-billing-and-quotas.md) | Экономика flat-тарифа, тарифная сетка, квоты, метеринг, неоплата, платёжки РФ |
| [14-frontend-console.md](14-frontend-console.md) | React SPA, BFF, CSP, дерево экранов, вайрфреймы, админка оператора |
| [15-observability-and-operations.md](15-observability-and-operations.md) | SLO, алерты, логи/метрики тенантов, backup/DR, HA, ёмкость, runbooks, соло on-call |
| [16-legal-ru.md](16-legal-ru.md) | 406-ФЗ (реестр хостинг-провайдеров), СОРМ, 152-ФЗ, 54-ФЗ, оферта/AUP, чек-лист до первого клиента |
| [17-roadmap.md](17-roadmap.md) | Этапы, оценки, состав MVP, фаза 2, критерии «можно брать деньги» |
| [18-risks-and-owner-decisions.md](18-risks-and-owner-decisions.md) | Реестр рисков, список решений для владельца с рекомендациями |
| [19-requirements.md](19-requirements.md) | Функциональные и нефункциональные требования с ID, приоритетами и этапами |

## Условные обозначения

- **D1…D15** — зафиксированные архитектурные решения (индекс — [01 §12](01-architecture-overview.md)).
- **R-\*** — уточнения D-решений, появившиеся при детальной проработке разделов; имеют приоритет над исходной формулировкой ([01 §12.1](01-architecture-overview.md)).
- **P1…P9** — проблемы текущего прода, найденные попутно, независимо от PaaS ([18 §0](18-risks-and-owner-decisions.md)).
- **Q1…Q36** — решения, которые нужны от владельца, с рекомендациями ([18 §2](18-risks-and-owner-decisions.md)).
- **⚠️ проверить** — факт, зависящий от версии или даты (статус фичи в k8s 1.36, лимиты Let's Encrypt, покрытие API SeaweedFS 4.45, цены). Веб-поиск в ходе работы был ограничен — эти места нужно сверить на стенде или по документации до принятия решения. Рядом указано, как именно проверить.
- **Ф2** — фаза 2 (после платного запуска).
- Имена-заглушки: `<platform-domain>` — домен консоли, `<apps-domain>` — отдельный домен приложений тенантов, префикс label'ов `paas.1520.tech/*`.

## Как это делалось

Проверенные факты о кластере (версии, топология, StorageClass'ы, ограничения namespace-scoped ArgoCD, `cluster-base`, bastion-proxy, Vault/bank-vaults, SeaweedFS) взяты напрямую из `hosts-vars/` и `reference/` этого репозитория. Юридические требования 406-ФЗ проверены веб-поиском. По первоисточникам (kubernetes.io, letsencrypt.org, docs.cilium.io, developer.hashicorp.com) проверены: user namespaces — stable с k8s 1.36 (нужны ядро ≥ 6.3, containerd ≥ 2.0, runc ≥ 1.2); ValidatingAdmissionPolicy — stable с 1.30; актуальные лимиты Let's Encrypt; Cilium Egress Gateway в OSS и его требования; `bound_service_account_namespace_selector` в Vault Kubernetes auth. Разделы 02-16 писались параллельно по единому набору решений D1-D15 и затем сверялись на противоречия.
