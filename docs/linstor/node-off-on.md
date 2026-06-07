документация: https://linbit.com/drbd-user-guide/linstor-guide-1_0-en/

Два независимых плана
- K8s (cordon/drain/piraeus-ha-controller — двигают ПОДЫ) и LINSTOR (физические реплики)
- Drain/cordon/HA-controller НЕ перемещают DRBD-реплики
- Перемещение реплики — всегда решение слоя LINSTOR

**Сценарий 1 (контролируемый вывод, replica-2):**
- порядок `cordon → drain → linstor node evacuate <node>`
- Запускать evacuate, ПОКА нода жива (данные синкаются с неё)
- Нода → статус `EVACUATE`, реплики уезжают, новые туда не приходят
- Возврат — `linstor node restore` (реплики сами не вернутся — оператора это устраивает) или `linstor node lost`.

**Сценарий 2 (внезапный отказ, replica-2):**
- auto-evict через `AutoEvictAfterTime` (60 мин) помечает ноду EVICTED и переназначает реплику на живую ноду
- `AutoEvictMaxDisconnectedNodes=34%` — тормоз против массовой эвакуации при сетевой аварии контроллера
- для 5 нод: 1 offline → эвикт; 2 offline → подавляется
- Возврат ноды — только `node restore`/`node lost` вручную (нода в EVICTED сама не переподключится)

# Сценарий-1: Контролируемый процесс вывода сервера на обслуживание

# 1. ЗАПРЕТИТЬ новые поды на ноде 3 (K8s-план)
kubectl cordon <node3>

# 2. ВЫГНАТЬ существующие поды с ноды 3 (K8s-план)
kubectl drain --ignore-daemonsets <node3>

# 3. УВЕСТИ физические реплики LINSTOR с ноды 3 (LINSTOR-план)
linstor node evacuate <node3>

## Важные замечания
нода 3 ещё работает  →  cordon + drain  →  linstor node evacuate  →  ЖДЁШЬ окончания sync  →  ТОЛЬКО ПОТОМ выключаешь сервер 3

«Новые реплики туда не попадают»
Это обеспечивает именно статус EVACUATE, а не cordon. Пока нода в EVACUATE:

новые реплики LINSTOR на неё не размещаются (это свойство самого состояния);
`cordon` отдельно держит то, что туда не сядут K8s-поды.
То есть твоя картина верна: после трёх шагов на сервере 3 нет подов (`cordon+drain`) + нет реплик (`evacuate`) + новые реплики не приходят (EVACUATE). И держится это состояние сколько угодно (дни/недели), пока ты сам не сделаешь restore или lost.

# 4. Как смотреть и проверять
linstor node list                      # node3 перейдёт Online → EVACUATE
linstor resource list                  # видно SyncTarget на node4, потом UpToDate
linstor resource list --nodes <node3>  # должен опустеть до нуля

# 5. Возврат в строй
`linstor node restore <node3>`
- снять EVACUATE, вернуть ноду в работу
- старые storage pool / интерфейсы сохранятся
- реплики назад сами НЕ приедут — это ФАКТ
`linstor node lost    <node3>`
- выкинуть ноду целиком вместе со всем, что было