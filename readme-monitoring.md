# Monitoring: ServiceMonitor

Полный список компонентов — какие ServiceMonitor можно создать, какие нельзя.
ServiceMonitor создаётся позже (после установки Prometheus Operator)

---

# ServiceMonitor — МОЖНО создать

## Cilium (NS = `cilium`)

1. cilium-agent-metrics
   1. 9962 (prometheus)
2. cilium-operator-metrics
   1. 9963 (prometheus)
3. cilium-envoy
   1. 9964
   2. ВАЖНО, тут node-ip (HEADLESS)
4. hubble-metrics
   1. 9965 (-, headless)
5. hubble-relay-metrics
   1. 9966 (metrics)

## пример

```
~ $ curl -s http://10.129.0.27:9962/metrics | head -5
# HELP cilium_act_processing_time_seconds time to go over ACT map and update the metrics
# TYPE cilium_act_processing_time_seconds histogram
cilium_act_processing_time_seconds_bucket{le="0.005"} 0
cilium_act_processing_time_seconds_bucket{le="0.01"} 0
cilium_act_processing_time_seconds_bucket{le="0.025"} 0

~ $ curl -s http://10.129.0.37:9963/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter

~ $ curl -s http://10.129.0.27:9964/metrics | head -5
# TYPE envoy_cluster_assignment_stale counter
envoy_cluster_assignment_stale{envoy_cluster_name="/envoy-admin"} 0
envoy_cluster_assignment_stale{envoy_cluster_name="egress-cluster"} 0
envoy_cluster_assignment_stale{envoy_cluster_name="egress-cluster-tls"} 0
envoy_cluster_assignment_stale{envoy_cluster_name="ingress-cluster"} 0

~ $ curl -s http://10.129.0.27:9965/metrics | head -5
# HELP grpc_server_handled_total Total number of RPCs completed on the server, regardless of success or failure.
# TYPE grpc_server_handled_total counter
grpc_server_handled_total{grpc_code="Aborted",grpc_method="Check",grpc_service="grpc.health.v1.Health",grpc_type="unary"} 0
grpc_server_handled_total{grpc_code="Aborted",grpc_method="GetAgentEvents",grpc_service="observer.Observer",grpc_type="server_stream"} 0
grpc_server_handled_total{grpc_code="Aborted",grpc_method="GetDebugEvents",grpc_service="observer.Observer",grpc_type="server_stream"} 0

~ $ curl -s http://10.64.1.55:9966/metrics | head -5
# HELP go_gc_duration_seconds A summary of the wall-time pause (stop-the-world) duration in garbage collection cycles.
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 3.4293e-05
go_gc_duration_seconds{quantile="0.25"} 5.0268e-05
go_gc_duration_seconds{quantile="0.5"} 5.7288e-05
```

---

## Cert-manager (NS = `cert-manager`)

1. cert-manager
   1. 9402
2. cert-manager-cainjector
   1. 9402
3. cert-manager-webhook
   1. 9402

## пример

```
~ $ curl -s http://10.141.218.19:9402/metrics | head -5
# HELP certmanager_clock_time_seconds DEPRECATED: use clock_time_seconds_gauge instead. The clock time given in seconds (from 1970/01/01 UTC).
# TYPE certmanager_clock_time_seconds counter
certmanager_clock_time_seconds 1.775402791e+09
# HELP certmanager_clock_time_seconds_gauge The clock time given in seconds (from 1970/01/01 UTC). Gauge form of the deprecated clock_time_seconds counter. No labels.
# TYPE certmanager_clock_time_seconds_gauge gauge
~ $ curl -s http://10.131.91.53:9402/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter
~ $ curl -s http://10.138.167.86:9402/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter
```

---

## External Secrets (NS = `external-secrets`)

1. external-secrets-metrics
   1. 8080
2. external-secrets-webhook-metrics
   1. 8080
3. external-secrets-cert-controller-metrics
   1. 8080

## пример

```
~ $ curl -s http://10.141.21.158:8080/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter
~ $ curl -s http://10.141.232.171:8080/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter
~ $ curl -s http://10.132.6.146:8080/metrics | head -5
# HELP certwatcher_read_certificate_errors_total Total number of certificate read errors
# TYPE certwatcher_read_certificate_errors_total counter
certwatcher_read_certificate_errors_total 0
# HELP certwatcher_read_certificate_total Total number of certificate reads
# TYPE certwatcher_read_certificate_total counter
```

---

## Traefik (`traefik-lb`)

1. traefik-metrics
   1. 9200 (порт metrics entrypoint)

## пример

```
~ $ curl -s http://10.130.230.167:9200/metrics | head -5
# HELP go_gc_duration_seconds A summary of the wall-time pause (stop-the-world) duration in garbage collection cycles.
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 4.5489e-05
go_gc_duration_seconds{quantile="0.25"} 9.8491e-05
go_gc_duration_seconds{quantile="0.5"} 0.000142218
```

---

## HAProxy (`haproxy-lb`)

1. haproxy-lb
   1. 1024 (название = stat)

## Пример

```
~ $ curl -s http://10.134.20.215:1024/metrics | head -5
# HELP haproxy_process_nbthread Number of started threads (global.nbthread)
# TYPE haproxy_process_nbthread gauge
haproxy_process_nbthread 2
# HELP haproxy_process_nbproc Number of started worker processes (historical, always 1)
# TYPE haproxy_process_nbproc gauge
```

---

## Longhorn (`longhorn-system`)

1. longhorn-backend
   1. 9500 (manager)

## Пример

```
~ $ curl -s http://10.141.40.204:9500/metrics | head -5
# HELP longhorn_disk_capacity_bytes The storage capacity of this disk
# TYPE longhorn_disk_capacity_bytes gauge
longhorn_disk_capacity_bytes{disk="default-disk-e347733a5e8a5e26",node="k8s-worker-2"} 4.0904388608e+10
# HELP longhorn_disk_reservation_bytes The reserved storage for other applications and system on this disk
# TYPE longhorn_disk_reservation_bytes gauge
```

---

## Vault (`vault`)

1. vault
   1. 8200 (название = http)
   2. Особенность: path: `/v1/sys/metrics`, params: `format=prometheus`

## для ServiceMomitor
endpoints:
  - port: http
    path: /v1/sys/metrics
    params:
      format: [prometheus]

## Пример

```
~ $ curl -s http://10.129.95.148:8200/v1/sys/metrics?format=prometheus | head -5
# HELP go_gc_duration_seconds A summary of the wall-time pause (stop-the-world) duration in garbage collection cycles.
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 1.0847e-05
go_gc_duration_seconds{quantile="0.25"} 4.4067e-05
go_gc_duration_seconds{quantile="0.5"} 0.000243982
```

---

## GitLab (`gitlab`)

1. gitlab-webservice-default
   1. 8083 (название = http-metrics-ws)
   2. 9229 (название = http-metrics-wh)
2. gitlab-gitaly
   1. 9236 (название = http-metrics)
   2. Особенность = HEADLESS
3. gitlab-gitlab-pages-metrics
   1. 9235 (название = metrics)
4. gitlab-gitlab-exporter
   1. 9168 (название = http-metrics)
5. gitlab-registry
   1. 5001 (название = http-metrics)

## Пример

```
~ $ curl -s http://10.128.239.153:9168/metrics | head -3
ruby_gc_stat_count 6
ruby_gc_stat_time 58
ruby_gc_stat_heap_allocated_pages 165
~ $ curl -s http://10.142.42.81:8083/metrics | head -3
# HELP action_cable_active_connections Multiprocess metric
# TYPE action_cable_active_connections gauge
action_cable_active_connections{pid="puma_0"} 0
~ $ curl -s http://10.142.42.81:9229/metrics | head -3
# HELP gitlab_build_info Current build info for this GitLab Service
# TYPE gitlab_build_info gauge
gitlab_build_info{built="20250814.160259",version="v17.11.7"} 1
~ $ curl -s http://10.141.251.131:5001/metrics | head -3
# HELP gitlab_build_info Current build info for this GitLab Service
# TYPE gitlab_build_info gauge
gitlab_build_info{built="2025-08-14T15:31:31",package="github.com/docker/distribution",revision="58c1654be980b5da0348d116b7e815c1b3f172f0",version="v4.19.2-gitlab"} 1
~ $ curl -s http://10.137.70.210:9235/metrics | head -3
# HELP gitlab_build_info Current build info for this GitLab Service
# TYPE gitlab_build_info gauge
gitlab_build_info{built="",version="v17.11.7"} 1
~ $ curl -s http://10.64.12.79:9236/metrics | head -3
# HELP gitaly_backup_bundle_bytes Size of a Git bundle uploaded in a backup
# TYPE gitaly_backup_bundle_bytes histogram
gitaly_backup_bundle_bytes_bucket{le="1"} 0
```

---

### GitLab Runner (`gitlab-runner`)

1. gitlab-runner
   1. 9252 (название = metrics)

## Пример

```
~ $ curl -s http://10.138.253.243:9252/metrics | head -5
# HELP gitlab_runner_api_request_duration_seconds Latency histogram of API requests made by GitLab Runner
# TYPE gitlab_runner_api_request_duration_seconds histogram
gitlab_runner_api_request_duration_seconds_bucket{endpoint="request_job",runner="ag_5FFs9f",system_id="r_iyFraZWvJX5p",le="0.1"} 8
gitlab_runner_api_request_duration_seconds_bucket{endpoint="request_job",runner="ag_5FFs9f",system_id="r_iyFraZWvJX5p",le="0.25"} 9
gitlab_runner_api_request_duration_seconds_bucket{endpoint="request_job",runner="ag_5FFs9f",system_id="r_iyFraZWvJX5p",le="0.5"} 9
```

---

### ArgoCD (`argocd`)

1. argocd-metrics
   1. 8082 (название = metrics)
2. argocd-server-metrics
   1. 8083 (название = metrics)
3. argocd-repo-server
   1. 8084 (название = metrics)
4. argocd-applicationset-controller
   1. 8080 (название = metrics)
5. argocd-dex-server
   1. 5558 (название = metrics)
6. argocd-notifications-controller-metrics
   1. 9001 (название = metrics)

## пример

```
~ $ curl -s http://10.132.117.63:8082/metrics | head -3
# HELP argocd_kubectl_rate_limiter_duration_seconds Kubectl rate limiter latency
# TYPE argocd_kubectl_rate_limiter_duration_seconds histogram
argocd_kubectl_rate_limiter_duration_seconds_bucket{host="10.128.0.1:443",verb="Get",le="0.005"} 7
~ $ curl -s http://10.139.18.243:8083/metrics | head -3
# HELP argocd_info ArgoCD version information
# TYPE argocd_info gauge
argocd_info{version="v3.0.6+db93798"} 1
~ $ curl -s http://10.132.209.69:9001/metrics | head -3
# HELP go_gc_duration_seconds A summary of the wall-time pause (stop-the-world) duration in garbage collection cycles.
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 5.0166e-05
~ $ curl -s http://10.139.126.94:8084/metrics | head -3
# HELP go_gc_duration_seconds A summary of the wall-time pause (stop-the-world) duration in garbage collection cycles.
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 5.168e-05
~ $ curl -s http://10.133.93.234:8080/metrics | head -3
# HELP argocd_kubectl_rate_limiter_duration_seconds Kubectl rate limiter latency
# TYPE argocd_kubectl_rate_limiter_duration_seconds histogram
argocd_kubectl_rate_limiter_duration_seconds_bucket{host="10.128.0.1:443",verb="Get",le="0.005"} 5
```

## Prometheus-operator (NS = `mon`)

1. metrics
   1. 8080 (HEADLESS)

## Пример

```
~ $ curl -s http://10.64.12.171:8080/metrics | head -5
# HELP go_gc_cycles_automatic_gc_cycles_total Count of completed GC cycles generated by the Go runtime. Sourced from /gc/cycles/automatic:gc-cycles.
# TYPE go_gc_cycles_automatic_gc_cycles_total counter
go_gc_cycles_automatic_gc_cycles_total 17
# HELP go_gc_cycles_forced_gc_cycles_total Count of completed GC cycles forced by the application. Sourced from /gc/cycles/forced:gc-cycles.
# TYPE go_gc_cycles_forced_gc_cycles_total counter
```

## Prometheus-instance (NS = `mon`)

1. 9090 — основной, здесь и API и /metrics (self-monitoring самого Prometheus)
2. 8080 — это sidecar config-reloader, он тоже отдаёт /metrics

```
~ $ curl -s http://10.143.146.202:9090/metrics | head -5
# HELP go_gc_cycles_automatic_gc_cycles_total Count of completed GC cycles generated by the Go runtime. Sourced from /gc/cycles/automatic:gc-cycles.
# TYPE go_gc_cycles_automatic_gc_cycles_total counter
go_gc_cycles_automatic_gc_cycles_total 18
# HELP go_gc_cycles_forced_gc_cycles_total Count of completed GC cycles forced by the application. Sourced from /gc/cycles/forced:gc-cycles.
# TYPE go_gc_cycles_forced_gc_cycles_total counter
~ $ curl -s http://10.143.146.202:8080/metrics | head -5
# HELP go_gc_cycles_automatic_gc_cycles_total Count of completed GC cycles generated by the Go runtime. Sourced from /gc/cycles/automatic:gc-cycles.
# TYPE go_gc_cycles_automatic_gc_cycles_total counter
go_gc_cycles_automatic_gc_cycles_total 4
# HELP go_gc_cycles_forced_gc_cycles_total Count of completed GC cycles forced by the application. Sourced from /gc/cycles/forced:gc-cycles.
# TYPE go_gc_cycles_forced_gc_cycles_total counter
```

---

## ServiceMonitor — НЕЛЬЗЯ создать (нет Service)

| Компонент | Порт | Причина | Что можно |
|-----------|------|---------|-----------|
| **GitLab Sidekiq** | 3807 | Chart не создаёт Service для метрик — только pod annotations | **PodMonitor** |
