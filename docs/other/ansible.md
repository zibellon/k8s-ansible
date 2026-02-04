## `become: true` -> Запускать команды от root (sudo)

## Разные системы вывода результат
`result.stdout` -> string	"Static hostname: manager-1\nIcon name: computer-vm\n..."
`result.stdout_lines` -> list	["Static hostname: manager-1", "Icon name: computer-vm", ...]
stdout — весь вывод как одна строка (с \n)
stdout_lines — тот же вывод, но разбитый на список строк

## `changed_when: false` говорит Ansible: "эта задача никогда не меняет состояние системы"

Проблема без changed_when: false:
- Модуль `command` всегда возвращает `changed: true`, потому что Ansible не знает, изменила команда что-то или нет

TASK [Check swap status] *******changed: [manager-1]    ← Ansible думает, что что-то изменилосьchanged: [worker-1]
Но `swapon --show` — это просто чтение информации, ничего не меняет.

С `changed_when: false`:
TASK [Check swap status] *******
ok: [manager-1]    ← Правильно, ничего не изменилось
ok: [worker-1]
TASK [Check swap status] *******ok: [manager-1]    ← Правильно, ничего не изменилосьok: [worker-1]

## Зачем результат у команды `copy`

`register: modules_conf` сохраняет результат выполнения модуля `copy`
Зачем нужно:
Дальше в playbook:
- name: Reboot servers  reboot:    ...  when: modules_conf.changed    # ← Используется здесь
modules_conf.changed — булево значение:
- true — файл был создан или изменён
- false — файл уже существовал с таким же содержимым
Что содержит modules_conf:
modules_conf:
- changed: true/false      # Был ли файл изменён
- dest: "/etc/modules-load.d/k8s.conf"
- checksum: "abc123..."    # SHA1 хеш файла
- uid: 0
- gid: 0
- mode: "0644"
- state: "file"  # ... и другие поля
Логика:
Файл изменился → changed: true → делаем reboot
Файл уже был такой → changed: false → reboot пропускаем
Это даёт идемпотентность — при повторном запуске ребут не произойдёт.

## `changeg_when: XXX`
Нет, для `debug` не нужно
Модуль `debug` всегда возвращает `changed: false` по умолчанию — он только выводит информацию и ничего не меняет на сервере.
changed_when: false нужен только для модулей, которые по умолчанию возвращают changed: true:
- command
- shell
- raw
Модули, которые сами правильно определяют changed:
- debug — всегда ok
- copy — changed если файл изменился
- file — changed если права/владелец изменились
- apt — changed если пакет установлен
- systemd — changed если состояние изменилось
Для них `changed_when` не нужен.

## Справка по systemd
systemd -> systemctl
name: systemd-modules-load -> systemctl ... systemd-modules-load
state: restarted -> systemctl restart
state: started -> systemctl start
state: stopped -> systemctl stop
enabled: yes -> systemctl enable
enabled: no -> systemctl disable
daemon_reload: yes -> systemctl daemon-reload

## Unarchive
`remote_src: yes` -> говорит Ansible что архив уже на удалённом сервере (в /tmp/containerd.tar.gz), а не на локальной машине
- `remote_src: no` (default)	С твоей машины (где запускается Ansible)
- remote_src: yes	С удалённого сервера
### Пример
### `get_url` скачивает архив на сервер в /tmp/containerd.tar.gz
### `unarchive` распаковывает его там же на сервере → нужен `remote_src: yes`
### Без `remote_src: yes` Ansible будет искать /tmp/containerd.tar.gz на твоём Mac и пытаться передать его на сервер.

## Как отключить сбор информации о хосте
`gather_facts: false` — отключает автоматический сбор информации о хосте.
По умолчанию Ansible перед выполнением задач собирает "факты" о сервере:
- ОС, версия ядра
- IP-адреса, сетевые интерфейсы
- CPU, RAM, диски
- и т.д.
Эти данные доступны через переменные
- ansible_os_family
- ansible_hostname
- ansible_default_ipv4.address
Зачем отключать:
Скорость — сбор фактов занимает 2-5 секунд на каждом хосте
Не нужны — если playbook не использует эти переменные
Меньше нагрузки — особенно при работе с большим количеством хостов
Когда нужно gather_facts: true:
Когда используешь условия типа when: ansible_os_family == "Debian"
Когда нужен IP хоста через ansible_default_ipv4.address
Когда логика зависит от характеристик системы
В наших playbooks мы не используем факты — все переменные берём из hosts.yaml, поэтому gather_facts: false экономит время.

# что собирает gather_facts

Основные категории переменных (при gather_facts: true):
Сеть
- ansible_hostname — имя хоста
- ansible_fqdn — полное доменное имя
- ansible_default_ipv4.address — основной IPv4 адрес
- ansible_all_ipv4_addresses — список всех IPv4
- ansible_interfaces — список сетевых интерфейсов
- ansible_<interface> — детали по каждому интерфейсу (ip, mac, mtu)
ОС
- ansible_os_family — семейство ОС (Debian, RedHat, etc.)
- ansible_distribution — дистрибутив (Ubuntu, CentOS)
- ansible_distribution_version — версия дистрибутива
- ansible_distribution_major_version — мажорная версия
- ansible_kernel — версия ядра
- ansible_architecture — архитектура (x86_64, arm64)
Железо
- ansible_processor — информация о CPU
- ansible_processor_cores — количество ядер
- ansible_processor_vcpus — количество vCPU
- ansible_memtotal_mb — общая RAM в MB
- ansible_memfree_mb — свободная RAM
- ansible_swaptotal_mb — размер swap
Диски
- ansible_devices — блочные устройства
- ansible_mounts — смонтированные FS
Пользователь/Окружение
- ansible_user_id — текущий пользователь
- ansible_user_dir — домашняя директория
- ansible_env — переменные окружения
- ansible_python_version — версия Python
Прочее
- ansible_date_time — текущая дата/время
- ansible_virtualization_type — тип виртуализации (kvm, docker, etc.)
- ansible_selinux — статус SELinux
- ansible_pkg_mgr — пакетный менеджер (apt, yum, dnf)
- ansible_service_mgr — менеджер служб (systemd)


Текущее поведение (block + throttle: 1):
  task1: host1 → host2 → host3
  task2: host1 → host2 → host3

Нужное поведение (loop + include_single_task_file):
  host1: task1 → task2 → task3
  host2: task1 → task2 → task3