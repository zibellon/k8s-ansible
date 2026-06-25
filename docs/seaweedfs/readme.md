
# как работает алгоритм EC - erause-coding
1. В бесплатной версии OSS - если только алгоритм 10+4
   1. То есть: нельзя сделать других распределений
2. Механизм EC - работает не на лету, а запускается руками - через cli


Что надо описать
- как работает квота
- как работает EC алгоритм
  - как собрать
  - как проверить
  - как разобрать (если надо что-то удалить физически с диска)
- как работает тот большой файл на 30гб по дефолту
- как работает авторизация
  - user + keys (AccesKey | SecretKey)
  - bucket
  - bucket-policy (IAM - на официальных политиках от AWS)
  - bucket-policy - через user-creds (там можно INLINE их настраивать)


Как работает квота - ее надо через крону тыкать, она просто так постоянно не работает


Запустить EC - чтобы раскидать по нодам
переходит в Read-only
потом оттуда что-то удаляется и удаляется много
запустить процесс восстановления EC - обратно в реплику
запустить команду для очистки реплики
снова сделать EC.build



по дефолту - один файл = 30_000 MB (Один большой файл .dat)



как работает логика
- user+keys
  - есть структура пользователей, которые нужны в SeaweedFS
  - то, что нам надо - хранится в YAML файле в ansible
  - а то, что реально в кластере и примонтировано в POD как k8s.secret = находнится в VAULT (один большой файл)
  - запускаем синхронизацию пользователей
  - достаем из vault - что там есть (оно может быть а может и не быть). Один большой JSON файл
  - вычисляем DIFF между тем что в VAULT и тем что в YAML (ansible)
  - для пользователей, которые есть в vault но нет в Ansible - удаляем из финального JSON
  - для пользователей у которых что-то поменялось (правда я не знаю что) - обновляем в JSON
  - для новых пользователей - генерируем ключи и добавляем в JSON
  - Сохраняем в VAULT этот JSON
- buckets + policy
  - 
- user-distribute-creds



полезные команды

- топология кластера
  - `kubectl exec -n seaweedfs seaweedfs-master-0 -- sh -c 'echo "volume.list" | weed shell -master=seaweedfs-master:9333'`
- какой-то статус
  - `kubectl exec -n seaweedfs seaweedfs-master-0 -- curl -s http://localhost:9333/dir/status`
- еще один статус
  - `kubectl exec -n seaweedfs seaweedfs-master-0 -- curl -s http://localhost:9333/cluster/status`


напечатать текущий конфиг
`echo "fs.configure" | kubectl -n seaweedfs exec -i deploy/seaweedfs-s3 -- weed shell -master=seaweedfs-master:9333 -filer=seaweedfs-filer:8888`

Текущий конфиг по пользователям
`echo "s3.configure" | kubectl -n seaweedfs exec -i deploy/seaweedfs-s3 -- weed shell -master=seaweedfs-master:9333 -filer=seaweedfs-filer:8888`

Информация по бакетам
`echo "s3.bucket.list" | kubectl -n seaweedfs exec -i deploy/seaweedfs-s3 -- weed shell -master=seaweedfs-master:9333 -filer=seaweedfs-filer:8888`

информация по policy
`echo "s3.policy -list" | kubectl -n seaweedfs exec -i deploy/seaweedfs-s3 -- weed shell -master=seaweedfs-master:9333 -filer=seaweedfs-filer:8888`

информация по volumes
`kubectl -n seaweedfs exec -i seaweedfs-master-0 -- sh -c 'echo "volume.fix.replication -verbose" | weed shell'`

1. Что такое volumes.<group>.replicas и что будет при replicas=10 на 5 серверах
replicas группы → это spec.replicas StatefulSet'а (volume-statefulset.yaml:26) = число pod'ов volume-сервера в этой группе. Каждый pod — отдельный volume-сервер, со своим PVC (через volumeClaimTemplates) на своей ноде.

Ключевой факт: в чарте по умолчанию (values.yaml:479) включена жёсткая anti-affinity:

podAntiAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector: { ... component: volume-<group> }
      topologyKey: kubernetes.io/hostname
То есть максимум 1 pod группы на одну ноду. Плюс podManagementPolicy: Parallel (все pod'ы создаются разом).

Сценарий replicas=10, а worker-нод 5:

5 pod'ов сядут (по одному на ноду), 5 останутся в Pending навсегда — anti-affinity не пускает второй pod на занятую ноду, свободных нод нет.
helm --wait / tasks-wait-rollout ждут готовности всего StatefulSet'а → timeout → установка падает.
Правило: replicas ≤ числа нод, которые матчит nodeSelector. 5 worker'ов → replicas: 5. 3 manager'а → replicas: 3.

(Если отключить anti-affinity — affinity: "" — то 10 pod'ов упакуются по 2 на ноду → 2 volume-сервера на одном физическом диске = конкуренция за I/O + падение ноды убивает сразу 2 «реплики». Для хранилища так делать не надо.)