# Устаревшее

Компоненты, которые больше не используются. Оставлены для истории: код в репозитории ещё есть, но в актуальный сценарий установки они не входят.

Основной документ — [`README.md`](README.md).

---

## Longhorn

> ⚠️ **DEPRECATED.** В качестве хранилища используется [LINSTOR](README.md#linstor). Два storage-стека в кластере параллельно не ставятся — выбирается ровно один.

**Чарт:** официальный upstream.

- Есть UI, доступный по URL.
- Автоматически подхватывает конфиг: обновили `ConfigMap` — сразу подхватил и начал использовать.
- `namespace: longhorn-system` — **менять нельзя**, так написано в документации.
- Пример обновлённого конфига — `docs/longhorn/other/...`.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/longhorn-install.yaml`](playbook-app/longhorn-install.yaml).
- Есть создание секретов для бэкапа в S3 — через CRD от ESO. Сразу работать они не будут: секреты появляются в Vault позже.
- Есть работа с ESO.

**Важно 1.** Секреты для работы с backup нужно определить в `hosts-vars-override/` (пример — в `hosts-vars-example/`). После определения они будут использоваться при установке `longhorn-install.yaml`.

**Важно 2.** `node-tags`: для их автоматической установки на ноды есть отдельный playbook. Синхронизация вызывается **отдельно** — после установки Longhorn, после добавления ноды и после изменения `node-tags` в `hosts-vars-*`.

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/longhorn-install.yaml

# синхронизация всех node-tags — у CRD-объекта nodes.longhorn.io
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/longhorn-tags-sync.yaml
```

**Выключение.** В `hosts-vars-override/<cluster>/longhorn.yaml` держится явный `longhorn_enabled: false` — чтобы выбор storage-стека был осознанным, а не следствием дефолта.
