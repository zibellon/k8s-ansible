# установить на устройство `rclone`

Инструкция по установке = `https://rclone.org/install/`

# Задача - пернести ОДИН s3 бакет (например minio) в ДРУГОЙ s3 бакет (Seaweedfs)

## Port-forward
## терминал A — старый minio  (при двух кластерах: добавь --context <OLD>)
`kubectl -n ns-gitlab  port-forward svc/svc-gitlab-minio 9000:9000`

## туннель к СТАРОМУ серверу → его порт-форвард minio
`ssh -N -L 9000:localhost:9000 ubuntu@<IP_СТАРОГО_СЕРВЕРА>`

## терминал B — новый seaweedfs (при двух кластерах: добавь --context <NEW>)
`kubectl -n seaweedfs  port-forward svc/seaweedfs-s3     8333:8333`

## в другом окне: туннель к НОВОМУ серверу → его порт-форвард seaweedfs
`ssh -N -L 8333:localhost:8333 ubuntu@<IP_НОВОГО_СЕРВЕРА>`

## Сам перенос

- создаем конфиг, `rclone.conf` (можно прям в корне - `~/`. Пример в файле = `./rclone.conf`)
- можно узнать список бакетов
  - `rclone --config ./rclone.conf lsd oldhost:`
  - `rclone --config ./rclone.conf lsd newhost:`
- Узнаем развер бакета для переноса: `rclone --config ./rclone.conf size oldhost:<bucket_name>`
- команда для переноса
  - --metadata. Переносит метаданные объекта (system: Content-Type, Content-Encoding, cache-control + user-metadata x-amz-meta-*). Без него часть объектов может приехать с дефолтным application/octet-stream
  - --checksum. Меняет критерий «файл уже есть, пропустить». По умолчанию rclone сравнивает size + mod-time. С --checksum — сравнивает size + хеш (ETag/MD5), игнорируя время модификации. Между двумя S3-бэкендами mod-time часто не совпадает (у S3 нет настоящего mtime), поэтому по времени rclone бы гонял всё заново. Хеш = надёжный skip уже скопированного при повторных запусках
  - --fast-list. Листит бакет рекурсивно за меньшее число API-запросов вместо покаталожного обхода. Быстрее и дешевле по числу LIST-операций на больших бакетах ценой большей памяти (держит весь листинг в RAM)
  - --s3-no-check-bucket. Отключает предварительную проверку/создание бакета (HeadBucket / CreateBucket) перед заливкой. Плюсы: экономит запрос и не падает, если у ключа нет прав на создание бакета или бэкенд плохо отвечает на HeadBucket.
  - --transfers 4. Сколько файлов заливается одновременно (реальная передача данных). Дефолт тоже 4. Крутить вверх для мелких объектов ускоряет; для S3-за-CDN осторожно с rate-limit
  - --checkers 8. Сколько сравнений source↔dest идёт параллельно (шаг «нужно ли вообще передавать этот объект» — читает листинги/хеши, данные не льёт). Дефолт 8. Это отдельный от --transfers пул: checkers идут впереди и наполняют очередь для transfers
  - --progress (он же -P). Живой прогресс в терминале: скорость, ETA, счётчик перенесённых/оставшихся, текущие файлы. Без него — тихо, статистика только в конце.
- верификация: `rclone --config ./rclone.conf check oldhost:registry newhost:gitlab-registry --download --one-way --fast-list`
- верификация_2 (размер)
  - `rclone --config ./rclone.conf size oldhost:registry`
  - `rclone --config ./rclone.conf size newhost:gitlab-registry`

rclone --config ./rclone.conf copy oldhost:registry newhost:gitlab-registry \
  --metadata --checksum --fast-list --s3-no-check-bucket \
  --transfers 4 --checkers 8 --progress
