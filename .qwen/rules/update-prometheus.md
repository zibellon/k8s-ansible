# Rule: Update Alertmanager + Prometheus Config
# Trigger keywords: обнови alertmanager, обнови prometheus конфиг, alertmanager config, prometheus config
# Manual trigger: /rule update-prometheus

## Когда применяется
Пользователь просит обновить конфигурацию Alertmanager или Prometheus (правила алертов, receivers, routing, scrape configs, и т.д.).

## Чеклист шагов

### Шаг 1: Определить, что обновить

**Alertmanager:**
- Receivers (email, Slack, PagerDuty, webhook)
- Route (routing tree, matchers, group_by)
- Inhibit rules
- Mute time intervals

**Prometheus:**
- Scrape configs (targets, intervals)
- Recording rules
- Alerting rules
- Storage retention
- Remote write/read

### Шаг 2: Обновить конфигурацию

```yaml
# hosts-vars-override/mon-prometheus-operator.yaml

# Alertmanager config
prometheus_alertmanager_config:
  - receiver: "email-notifications"
    route:
      group_by: ['alertname', 'namespace']
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 4h
    receivers:
      - name: "email-notifications"
        email_configs:
          - to: "alerts@example.com"
            from: "prometheus@example.com"
            smarthost: "smtp.example.com:587"

# Prometheus rules
prometheus_rules_extra:
  - name: "custom-alerts"
    groups:
      - name: "example"
        rules:
          - alert: HighCPUUsage
            expr: node_cpu_seconds_total{mode="idle"} < 0.1
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "High CPU usage detected"

# Prometheus scrape configs
prometheus_scrape_configs_extra:
  - job_name: "my-app"
    scrape_interval: 30s
    static_configs:
      - targets: ["my-app.default.svc:8080"]
```

### Шаг 3: Применить конфигурацию

```bash
# Полный install (обновит ConfigMaps)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-prometheus-operator-install.yaml

# Только install (без CRDs)
ansible-playbook ... --tags install
```

**Что произойдёт:**
1. Обновятся ConfigMaps с новыми конфигами
2. Prometheus/Alertmanager подхватят новые конфиги (hot-reload)
3. Новые правила алертов применятся

### Шаг 4: Проверить, что применилось

```bash
# Проверить ConfigMaps
kubectl get configmap -n monitoring | grep prometheus
kubectl get configmap -n monitoring | grep alertmanager

# Проверить Prometheus rules
kubectl get prometheusrule -n monitoring

# Проверить Alertmanager config
kubectl get alertmanager -n monitoring

# Проверить, что Prometheus видит правила
# Зайти в Prometheus UI → Status → Rules

# Проверить Alertmanager receivers
# Зайти в Alertmanager UI → Status → Receivers
```

### Шаг 5: Проверить алерты

```bash
# Проверить активные алерты
# Prometheus UI → Alerts

# Проверить, что Alertmanager получает алерты
# Alertmanager UI → Alerts

# Проверить, что уведомления отправляются
# Проверить логи Alertmanager
kubectl logs -n monitoring statefulset/alertmanager-main
```

## Важные нюансы

### Prometheus подхватывает конфиги автоматически
- ConfigMaps → hot-reload (не нужен restart)
- Но если добавить новые scrape targets →可能需要 подождать scrape_interval

### Alertmanager подхватывает конфиги автоматически
- ConfigMaps → hot-reload (не нужен restart)
- Route tree обновляется сразу

### Если что-то не применилось
```bash
# Проверить, что ConfigMap обновился
kubectl get configmap prometheus-prometheus-operator-prometheus-rulefiles-0 -n monitoring -o yaml

# Проверить логи Prometheus
kubectl logs -n monitoring prometheus-prometheus-operator-prometheus-0

# Проверить логи Alertmanager
kubectl logs -n monitoring alertmanager-main-0

# Если нужно — перезапустить
ansible-playbook ... playbook-app/mon-prometheus-operator-restart.yaml
```

## Типичные сценарии

### Сценарий 1: Добавить Slack notifications
```yaml
prometheus_alertmanager_config:
  - receiver: "slack-notifications"
    receivers:
      - name: "slack-notifications"
        slack_configs:
          - api_url: "https://hooks.slack.com/services/XXX/YYY/ZZZ"
            channel: "#alerts"
            text: "{{ .CommonAnnotations.summary }}"
```

### Сценарий 2: Добавить alert rule
```yaml
prometheus_rules_extra:
  - name: "node-alerts"
    groups:
      - name: "node"
        rules:
          - alert: NodeDown
            expr: up{job="node-exporter"} == 0
            for: 5m
            labels:
              severity: critical
            annotations:
              summary: "Node {{ $labels.instance }} is down"
```

### Сценарий 3: Добавить scrape target
```yaml
prometheus_scrape_configs_extra:
  - job_name: "my-app"
    scrape_interval: 30s
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names: ["default"]
        selectors:
          - role: pod
            label: "app=my-app"
```

## Валидация

### Проверить, что правила валидны
```bash
# Prometheus UI → Status → Rules
# Проверить, что нет ошибок валидации

# Alertmanager UI → Status → Config
# Проверить, что конфиг валиден
```

### Проверить, что алерты работают
```bash
# Trigger test alert
# Проверить, что алерт появился в Prometheus UI
# Проверить, что Alertmanager получил алерт
# Проверить, что уведомление отправлено (Slack/email)
```
