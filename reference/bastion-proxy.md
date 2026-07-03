# bastion-proxy — внешний edge-прокси (HAProxy + PROXY Protocol v2, L7 + L4)

Внешний HAProxy-сервер (Ubuntu-пакет, PPA vbernat) перед кластером: клиенты видят только IP bastion-proxy, реальные IP worker-нод скрыты. Обходит лимит Cloudflare 100 MB на тело запроса (grey-cloud → bastion-proxy → кластер) и даёт внешний доступ к TCP-сервисам (Postgres/Redis/gitlab-ssh). Настраивается целиком через Ansible (`playbook-system/bastion-proxy-install.yaml`). НЕ является k8s-нодой.

> **НЕ путать с SSH-ProxyJump `is_bastion`** ([`variables.md`](variables.md) §2.14/§2.14.1). Тот bastion — jump-host для приватных нод (флаг `is_bastion`, `bastion_host_fact`, reboot-last). `bastion_proxy` — внешний edge-прокси, отдельная концепция; хост `bastion_proxy` **никогда** не ставит `is_master`/`is_bastion`/`api_server_*`/`longhorn_*`/`node_labels`.

## 1. Inventory-группа `bastion_proxy`

Хосты в группе `bastion_proxy` (скелет — [`hosts-vars/hosts.yaml`](../hosts-vars/hosts.yaml); реальные значения — `hosts-vars-override/<cluster>/hosts.yaml`). Контракт переменных — [`variables.md`](variables.md) §2.15.

- **Rule 1 — N серверов.** Группа держит 2-3-N хостов; все получают одинаковый декларативный HAProxy-конфиг из одного playbook'а. Cluster-side allowlist'ы (NetworkPolicy `ipBlock`, git-ops) перечисляют IP **всех** bastion_proxy — список.
- **Rule 2 — белый IP.** Каждый хост всегда доступен по публичному IP напрямую: прямой SSH, БЕЗ ProxyJump / `ansible_ssh_common_args`.
- Минимум host-vars: `ansible_host` (белый IP), `ansible_user`, auth, `new_hostname`.

## 2. Playbook `playbook-system/bastion-proxy-install.yaml`

Один playbook, `hosts: bastion_proxy`, `become: true`, `gather_facts: false`. **Весь код inline** (без `include_tasks` / общих `tasks-*`) — намеренно ([`playbook-conventions.md`](playbook-conventions.md) §8.5); вынос в утилиты — отдельным шагом позже. Инвокации — [`commands-reference.md`](commands-reference.md) §4.10.

Теги выполняются линейно:

| Тег | Что делает |
|---|---|
| `node-install` | base packages + fail2ban jail + sshd hardening (key-only, `sshd -t`, reload) + sysctl fd-limits (`fs.file-max`/`fs.nr_open`) |
| `haproxy-install` | HAProxy из PPA vbernat → `apt-mark hold` → systemd `LimitNOFILE` override → **restart** (systemd-лимит применяется только при restart, не reload) |
| `haproxy-config` | рендер `/etc/haproxy/haproxy.cfg` из vars → **отдельный шаг** `haproxy -c -f` (validate) → **hitless reload** (`systemctl reload`, НЕ restart) при изменении — не рвёт долгие TCP/DB-сессии |
| `verify` (не `always`) | ассерты: `haproxy -v`, is-active, `LimitNOFILE` применён (`/proc`), sshd `PasswordAuthentication no`, слушает 80/443 + границы L4-диапазона |

## 3. Vars-модель (`hosts-vars/bastion-proxy-haproxy.yaml`)

Вся декларативная начинка — пакеты, fail2ban jail, sshd hardening, sysctl, systemd override, полный `haproxy.cfg` — живёт как `bastion_proxy_haproxy_*` переменные; playbook держит только структуру задач. Конфиг-переменные используют вложенные `{{ }}`-ссылки (Ansible резолвит рекурсивно). Полный каталог — [`variables.md`](variables.md) §2.15a.

**REQUIRED overrides:** `bastion_proxy_haproxy_l7_target_ip` и `_l4_target_ip` по умолчанию `""` — `haproxy -c` упадёт, пока не заданы в `hosts-vars-override/<cluster>/` (реальные worker/VIP IP — per-cluster).

## 4. Поток трафика

```
L7 (домены, grey-cloud → bastion-proxy):
  client → bastion:443/:80 (HAProxy mode tcp)
    → send-proxy-v2 → worker:NodePort (Traefik web/websecure, уже trust proxyProtocol)
    → Traefik терминирует TLS, видит реальный client IP, роутит по SNI/Host,
      per-domain ipAllowList (vpn-only) → app
    cert: bastion:80 → Traefik web → ACME HTTP-01 как сейчас (cert-manager не трогаем)

L4 (Postgres/Redis/ssh, A-запись → bastion-proxy):
  client → bastion:<port 10000-30000> (range bind, portless 1-1)
    → send-proxy-v2 → worker:NodePort (externalTrafficPolicy: Local → виден IP bastion)
    → in-cluster haproxy-ingress accept_proxy (снимает PROXY) → чистый TCP → Service БД
```

bastion конфигурируется один раз (80/443 + диапазон); новые сервисы добавляются только на стороне кластера (ArgoCD). in-cluster L4-приёмник — git-ops (§6).

## 5. Cilium host-firewall — правка НЕ нужна

CCNP host-firewall (`host-firewall-base`) распространяется **только** на (1) host-процессы и (2) hostNetwork-поды. Он **НЕ** гейтит трафик NodePort → non-hostNetwork под.

Трафик bastion-proxy терминируется на NodePort → Traefik (DaemonSet + NodePort, non-hostNetwork) / haproxy-ingress (DaemonSet) — обычные поды → под host-firewall **не попадают**.

**Эмпирическое доказательство:** внешний HTTPS доходит до Traefik уже сейчас, хотя `host-firewall-base` ingress пускает `world` только на SSH+ICMP. Если бы host-firewall гейтил NodePort→pod — текущий web был бы заблокирован; он не заблокирован.

Итог: **никаких правок CCNP / `values.yaml` / `hosts-vars/cilium.yaml` для bastion-proxy не требуется.** Детали scope host-firewall — [`networking.md`](networking.md) §2.

## 6. L4-приёмник (`accept_proxy`) + граница scope

- **gitlab / teleport L4 — `accept_proxy` флаг (В k8s-ansible):** их haproxy `kind: TCP` ingress несёт настраиваемый per-TCP boolean-флаг `accept_proxy` (default `false` = прямой доступ, как на `test-1`; `true` = за bastion-proxy → снимает PROXY-заголовок, чистый TCP в backend). Флаги: `gitlab_ssh_accept_proxy`; teleport `teleport_ssh_accept_proxy` / `_reverse_tunnel_accept_proxy` / `_kube_accept_proxy` — выставить `true` в `hosts-vars-override/<cluster>/`. `accept_proxy` СТРОГИЙ (в отличие от Traefik `proxyProtocol.insecure`): при `true` прямое подключение без PROXY-заголовка отвергается — нужен анти-байпас (ниже) либо гарантия, что весь трафик идёт через bastion.
- **Прочие L4-сервисы БД (git-ops репо `1520-tech-infra`, per-service ~500, ArgoCD):** haproxytech `kind: TCP` с `accept_proxy` (снимает PROXY-заголовок — БД его не понимают) → Service NodePort `externalTrafficPolicy: Local` (`nodePort` = порт сервиса в 10000-30000) → чистый TCP в Service БД.
- **Анти-байпас:** pod-level NetworkPolicy `ipBlock` (список **всех** bastion IP) на ingress-поде — правильная точка enforcement'а (pod-policy применяется к ingress-поду, в отличие от host CCNP). Тоже git-ops. **Обязателен** в паре с `accept_proxy` — иначе спуфинг client IP поддельным PROXY-заголовком.
- **Cloudflare DNS (вручную):** A-запись на IP bastion + grey-cloud (DNS-only) для тяжёлых L7-доменов; A-запись для L4.

## 7. Безопасность

- bastion — DDoS/скан-сборник перед кластером; **firewall на нём отсутствует** (ufw/iptables), фильтрация = HAProxy-уровень. Bastion **принимает все источники** (без src ACL) — per-domain L7-фильтр = Traefik `ipAllowList`; L4 client-allowlist — опционально, downstream.
- sshd — только по ключам (`PasswordAuthentication no`), fail2ban.
- FD: sysctl `fs.file-max`/`fs.nr_open` + systemd `LimitNOFILE` + haproxy `maxconn` — критично для range bind (20001 listen-сокетов; кратковременно ×2 при reload).
- Скрыть IP нод: DNS указывает только на bastion.

## 8. Исключение из cluster-операций

Существующие cluster-wide плейбуки сужены `hosts: all` → `managers:workers` (все `playbook-system/`) / `managers` (`teleport-restart`) / `managers:workers` (`teleport-ssh-agent-install`, `longhorn-tags-sync`), чтобы `bastion_proxy` не попадал в node/cluster/reboot-операции. Единственный `groups['all']`-цикл (`tasks-set-master-manager.yaml` «Find bastion host») фильтрует по `is_bastion`, который `bastion_proxy` не ставит. См. [`playbook-conventions.md`](playbook-conventions.md) §2.4.
