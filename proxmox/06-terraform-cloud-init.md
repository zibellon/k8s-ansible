# Terraform + cloud-init — VM Provisioning

Декларативное создание VM на Proxmox через Terraform, с автоматической настройкой OS через cloud-init. Нужно, потому что 12 VM руками — это ад, плюс воспроизводимость.

---

## 1. Архитектура решения

```
┌─────────────────────────────────────────────────────────┐
│  developer machine                                       │
│  ├── terraform/                                          │
│  │   ├── main.tf            ← VM definitions             │
│  │   ├── variables.tf                                    │
│  │   └── terraform.tfvars   ← API token, IPs, etc.       │
│  └── ssh keys                                            │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTPS (Proxmox API, 8006)
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Proxmox cluster                                         │
│  ├── VM template (id=9000) — Ubuntu 24.04 + cloud-init   │
│  │   создаётся ВРУЧНУЮ один раз                          │
│  ├── snippet storage — yaml files для cloud-init         │
│  └── target storage — LINSTOR RG (rg-net-sync etc.)      │
│                                                           │
│  При terraform apply:                                     │
│  1. Клонируется template → новая VM                      │
│  2. Linked clone сидит на LINSTOR storage                │
│  3. cloud-init применяет network/SSH/users               │
│  4. VM грузится готовая, доступна по SSH                 │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Выбор Terraform-провайдера

Есть два провайдера для Proxmox:

| Провайдер | Статус | Плюсы | Минусы |
|---|---|---|---|
| **`bpg/proxmox`** | активная разработка (2024+) | Современный, поддерживает Proxmox 8, cloud-init, file uploads | Чуть моложе |
| **`Telmate/proxmox`** | поддерживается, но slower releases | Долгая история, много примеров в Интернете | Меньше фич, странности с диффом state |

**Рекомендация:** **`bpg/proxmox`**. Активный maintainer, лучше работает с современным Proxmox API.

GitHub: https://github.com/bpg/terraform-provider-proxmox
Registry: https://registry.terraform.io/providers/bpg/proxmox/

---

## 3. Подготовка Proxmox

### 3.1 Создать API-пользователя и token

В Proxmox web UI:

1. **Datacenter → Permissions → Users → Add**
   - User: `terraform`
   - Realm: `pve` (Proxmox VE authentication)
   - Password: задать
2. **Datacenter → Permissions → API Tokens → Add**
   - User: `terraform@pve`
   - Token ID: `automation`
   - **Снять ✅ Privilege Separation** (token наследует все права user'а — иначе нужно отдельно настраивать ACL)
   - Сохранить отображённый **secret** — он показывается только один раз
3. **Datacenter → Permissions → Add → User Permission**
   - Path: `/`
   - User: `terraform@pve`
   - Role: `Administrator` (для production — узкий role с нужными правами; для bootstrap ОК)

Проверка через CLI:

```bash
curl -k -X GET \
    -H "Authorization: PVEAPIToken=terraform@pve!automation=<secret>" \
    https://94.126.207.67:8006/api2/json/version
# expected: {"data":{"version":"8.x.x","release":"x","repoid":"..."}}
```

### 3.2 Включить snippets storage

Для cloud-init файлов Terraform-провайдер загружает YAML-сниппеты в Proxmox storage. По умолчанию `local` storage не разрешает snippets — надо включить.

UI: **Datacenter → Storage → local → Edit → Content → ✅ Snippets**.

CLI alternative:

```bash
pvesm set local --content vztmpl,iso,snippets,backup,images
```

---

## 4. Создание VM template (один раз вручную)

Это базовый образ, который Terraform будет клонировать.

### 4.1 Скачать Ubuntu cloud image

```bash
# На любой ноде
cd /var/lib/vz/template/iso
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
# Это qcow2 image, готовый для cloud-init
```

### 4.2 Создать VM из image

```bash
# 9000 — id template'а; используем 9xxx range для templates
qm create 9000 \
    --name ubuntu-24.04-tmpl \
    --memory 2048 \
    --cores 2 \
    --net0 virtio,bridge=vmbr2 \
    --machine q35 \
    --bios ovmf \
    --efidisk0 linstor-local:0,efitype=4m,pre-enrolled-keys=0 \
    --scsihw virtio-scsi-single \
    --bootdisk scsi0 \
    --serial0 socket \
    --vga serial0 \
    --agent enabled=1 \
    --ostype l26
```

`linstor-local` — это LINSTOR storage с RG `rg-local-single` (см. [03-linstor-and-drbd.md](03-linstor-and-drbd.md) §11). Template на сетевой реплике не нужен — клоны будут пересоздаваться.

### 4.3 Импортировать диск

```bash
qm importdisk 9000 /var/lib/vz/template/iso/noble-server-cloudimg-amd64.img linstor-local

# После импорта — disk появится как unused0
qm set 9000 --scsi0 linstor-local:vm-9000-disk-1,iothread=1,ssd=1,discard=on,cache=writeback

# Cloud-init drive
qm set 9000 --ide2 linstor-local:cloudinit

# Boot order
qm set 9000 --boot order=scsi0
```

### 4.4 Проверить и сконвертировать в template

```bash
qm config 9000

# Запустить один раз руками для проверки (опционально)
# qm start 9000

# Конвертировать в template (immutable — нельзя стартовать)
qm template 9000
```

После `qm template` — VM становится непригодной для прямого запуска, но идеально подходит для клонирования.

### 4.5 Тест клонирования

```bash
# Полный клон
qm clone 9000 100 --name test-vm --full

# Linked clone (быстрее, занимает меньше)
qm clone 9000 100 --name test-vm

# Удалить тест
qm destroy 100
```

---

## 5. Минимальный Terraform пример

### 5.1 Структура каталога

```
terraform/
├── main.tf
├── variables.tf
├── terraform.tfvars              # gitignore!
├── modules/
│   └── proxmox-vm/
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
└── cloud-init/
    ├── user-data.tpl
    └── network-data.tpl
```

### 5.2 `main.tf` — provider config

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.60"
    }
  }
}

provider "proxmox" {
  endpoint  = var.pve_endpoint     # https://94.126.207.67:8006
  api_token = var.pve_api_token    # terraform@pve!automation=<secret>
  insecure  = true                 # self-signed cert; иначе настроить TLS

  ssh {
    agent    = true
    username = "root"
    private_key = file("~/.ssh/id_ed25519")
  }
}
```

### 5.3 `variables.tf`

```hcl
variable "pve_endpoint" {
  type        = string
  description = "Proxmox API endpoint URL"
}

variable "pve_api_token" {
  type        = string
  sensitive   = true
  description = "Proxmox API token (user@realm!tokenid=secret)"
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key для default user в VM"
}
```

### 5.4 `terraform.tfvars` (gitignored!)

```hcl
pve_endpoint  = "https://94.126.207.67:8006"
pve_api_token = "terraform@pve!automation=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
ssh_public_key = "ssh-ed25519 AAAA... me@laptop"
```

Добавить в `.gitignore`:

```
terraform/terraform.tfvars
terraform/.terraform/
terraform/*.tfstate
terraform/*.tfstate.backup
```

### 5.5 Module `proxmox-vm/main.tf` — переиспользуемый VM

```hcl
resource "proxmox_virtual_environment_vm" "this" {
  name        = var.name
  description = var.description
  node_name   = var.node
  vm_id       = var.vm_id

  agent {
    enabled = true
  }

  cpu {
    cores   = var.cores
    sockets = 1
    type    = "host"        # passthrough host CPU features
  }

  memory {
    dedicated = var.memory_mb
  }

  clone {
    vm_id        = var.template_id
    full         = false             # linked clone
    datastore_id = var.datastore_id
  }

  disk {
    interface    = "scsi0"
    datastore_id = var.datastore_id
    iothread     = true
    ssd          = true
    discard      = "on"
    cache        = "writeback"
    size         = var.disk_size_gb
  }

  network_device {
    bridge = var.bridge
    model  = "virtio"
  }

  initialization {
    datastore_id = var.snippets_datastore_id

    ip_config {
      ipv4 {
        address = "${var.ip_cidr}"
        gateway = var.gateway
      }
    }

    user_account {
      username = var.default_user
      keys     = [var.ssh_public_key]
    }

    dns {
      servers = ["8.8.8.8", "1.1.1.1"]
    }
  }

  serial_device {}

  operating_system {
    type = "l26"
  }

  machine = "q35"
  bios    = "ovmf"

  efi_disk {
    datastore_id = var.datastore_id
    type         = "4m"
  }
}
```

### 5.6 Module `proxmox-vm/variables.tf`

```hcl
variable "name" { type = string }
variable "description" { type = string; default = "" }
variable "node" { type = string }                       # node1, node2, node3
variable "vm_id" { type = number }
variable "template_id" { type = number; default = 9000 }
variable "datastore_id" { type = string }               # linstor-sync, linstor-async, linstor-local
variable "snippets_datastore_id" { type = string; default = "local" }
variable "cores" { type = number; default = 2 }
variable "memory_mb" { type = number; default = 4096 }
variable "disk_size_gb" { type = number; default = 50 }
variable "bridge" { type = string; default = "vmbr2" }
variable "ip_cidr" { type = string }                    # 10.0.1.10/24
variable "gateway" { type = string; default = "10.0.1.1" }
variable "default_user" { type = string; default = "ubuntu" }
variable "ssh_public_key" { type = string }
```

### 5.7 Использование module — `main.tf`

```hcl
module "k8s_master_1" {
  source = "./modules/proxmox-vm"

  name           = "k8s-master-1"
  node           = "node1"
  vm_id          = 110
  datastore_id   = "linstor-sync"          # critical: replica=2 + Proto C
  cores          = 4
  memory_mb      = 8192
  disk_size_gb   = 100
  ip_cidr        = "10.0.1.10/24"
  ssh_public_key = var.ssh_public_key
}

module "k8s_master_2" {
  source = "./modules/proxmox-vm"

  name           = "k8s-master-2"
  node           = "node2"
  vm_id          = 111
  datastore_id   = "linstor-sync"
  cores          = 4
  memory_mb      = 8192
  disk_size_gb   = 100
  ip_cidr        = "10.0.1.11/24"
  ssh_public_key = var.ssh_public_key
}

module "k8s_master_3" {
  source = "./modules/proxmox-vm"

  name           = "k8s-master-3"
  node           = "node3"
  vm_id          = 112
  datastore_id   = "linstor-sync"
  cores          = 4
  memory_mb      = 8192
  disk_size_gb   = 100
  ip_cidr        = "10.0.1.12/24"
  ssh_public_key = var.ssh_public_key
}

# Worker nodes — без жёсткого binding на ноду (LINSTOR расположит)
locals {
  workers = {
    "k8s-worker-1" = { vm_id = 120, ip = "10.0.1.20/24", node = "node1" }
    "k8s-worker-2" = { vm_id = 121, ip = "10.0.1.21/24", node = "node2" }
    "k8s-worker-3" = { vm_id = 122, ip = "10.0.1.22/24", node = "node3" }
    "k8s-worker-4" = { vm_id = 123, ip = "10.0.1.23/24", node = "node1" }
    "k8s-worker-5" = { vm_id = 124, ip = "10.0.1.24/24", node = "node2" }
    "k8s-worker-6" = { vm_id = 125, ip = "10.0.1.25/24", node = "node3" }
    "k8s-worker-7" = { vm_id = 126, ip = "10.0.1.26/24", node = "node1" }
    "k8s-worker-8" = { vm_id = 127, ip = "10.0.1.27/24", node = "node2" }
    "k8s-worker-9" = { vm_id = 128, ip = "10.0.1.28/24", node = "node3" }
  }
}

module "k8s_worker" {
  for_each = local.workers
  source   = "./modules/proxmox-vm"

  name           = each.key
  node           = each.value.node
  vm_id          = each.value.vm_id
  datastore_id   = "linstor-async"          # OS-disk не критичен → async
  cores          = 8
  memory_mb      = 16384
  disk_size_gb   = 200
  ip_cidr        = each.value.ip
  ssh_public_key = var.ssh_public_key
}
```

---

## 6. Workflow

### 6.1 Первый запуск

```bash
cd terraform/

# Инициализация
terraform init

# Посмотреть, что будет создано
terraform plan

# Применить
terraform apply

# (10-15 минут на 12 VM, в зависимости от storage)
```

После apply:
- 12 VM созданы и запущены
- На каждой настроены SSH-ключ, IP, hostname
- Можно подключаться: `ssh ubuntu@10.0.1.10`

### 6.2 Изменения

Любые изменения в `.tf` файлах:

```bash
terraform plan         # посмотреть diff
terraform apply        # применить
```

Важно: изменение определённых параметров (например, `disk_size_gb` уменьшение, или смена `datastore_id`) приведёт к пересозданию VM. Внимательно читать `plan`.

### 6.3 Удаление

```bash
terraform destroy
# (одобрить yes)
```

Удалит все VM и связанные DRBD-resources (LINSTOR plugin Proxmox автоматически очищает).

---

## 7. Cloud-init — что внутри

`bpg/proxmox` provider использует встроенный Proxmox cloud-init (через QEMU drive). Если нужны кастомные cloud-init файлы (для сложного user-data), можно загружать через snippets:

```hcl
resource "proxmox_virtual_environment_file" "user_data" {
  content_type = "snippets"
  datastore_id = "local"
  node_name    = "node1"

  source_raw {
    data = templatefile("${path.module}/cloud-init/user-data.tpl", {
      hostname = var.name
      ssh_key  = var.ssh_public_key
    })
    file_name = "user-data-${var.name}.yaml"
  }
}
```

Затем привязать в VM resource:

```hcl
initialization {
  user_data_file_id = proxmox_virtual_environment_file.user_data.id
  # ... остальное
}
```

`cloud-init/user-data.tpl`:

```yaml
#cloud-config
hostname: ${hostname}
manage_etc_hosts: true

users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${ssh_key}

package_update: true
package_upgrade: false

packages:
  - qemu-guest-agent
  - htop
  - curl
  - wget

runcmd:
  - systemctl enable --now qemu-guest-agent
  - timedatectl set-timezone UTC
```

Заменяет SSH-настройки, hostname, базовый набор пакетов. Для k8s-ansible bootstrap дополнительные пакеты установит сам Ansible.

---

## 8. Tips & Tricks

### 8.1 State в S3 / remote backend

Для команды — state вынести в remote backend:

```hcl
terraform {
  backend "s3" {
    endpoint                    = "https://minio.example.com"
    bucket                      = "terraform-state"
    key                         = "proxmox/main.tfstate"
    region                      = "us-east-1"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    force_path_style            = true
  }
}
```

### 8.2 SSH wait после создания

Cloud-init иногда заканчивается через 2-3 минуты после VM-старта. Если Terraform-зависимые ресурсы (например, Ansible через `null_resource`) выполняются раньше — провалятся.

```hcl
resource "null_resource" "wait_for_ssh" {
  depends_on = [module.k8s_master_1]

  provisioner "remote-exec" {
    inline = ["cloud-init status --wait"]

    connection {
      type        = "ssh"
      user        = "ubuntu"
      private_key = file("~/.ssh/id_ed25519")
      host        = "10.0.1.10"
    }
  }
}
```

### 8.3 Связь с k8s-ansible

После Terraform apply — VM готовы для bootstrap'а K8s через [k8s-ansible](../CLAUDE.md):

```bash
cd ../   # в k8s-ansible root

# Заполнить hosts-vars-override/hosts.yaml IP-адресами VM
# 10.0.1.10 → manager-1 (is_master: true)
# 10.0.1.11 → manager-2
# 10.0.1.12 → manager-3
# 10.0.1.20-28 → workers

# Запустить bootstrap
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
    playbook-system/node-install.yaml --limit manager-1
# и так далее по последовательности из bootstrap-and-ha.md
```

### 8.4 Не забывать про LINSTOR storage в Proxmox

Перед `terraform apply` storage `linstor-sync` / `linstor-async` / `linstor-local` должны быть зарегистрированы в Proxmox (см. [03-linstor-and-drbd.md](03-linstor-and-drbd.md) §11). Иначе Terraform падает на `datastore not found`.

### 8.5 VM IDs — резервируй диапазоны

Чтобы не было конфликтов:
- 100-199 — production K8s
- 200-299 — staging K8s
- 300-399 — dev/test
- 9000-9099 — templates

---

## 9. Troubleshooting

### 9.1 `connection refused` на API

```bash
# Проверить, что Proxmox API доступен
curl -k https://94.126.207.67:8006/api2/json/version

# Если 401 — проверить token
# Если timeout — firewall на стороне Proxmox / провайдера
```

### 9.2 `permission denied` при создании VM

Token не имеет нужных прав. В UI Datacenter → Permissions, проверь что для user `terraform@pve` есть Path=`/` Role=`Administrator` (или эквивалентный кастомный).

### 9.3 VM создалась, но cloud-init не сработал

```bash
# Подключиться к VM через Proxmox console
# В VM:
sudo cloud-init status        # должно быть "done" или "running"
sudo cloud-init logs --all    # подробный лог
```

Частые причины:
- IP конфликт в подсети
- Cloud-init drive не подключён (проверить `qm config <vmid> | grep ide2`)
- Snippets storage не настроен правильно

### 9.4 `terraform plan` показывает ненужные изменения

`bpg/proxmox` иногда видит «изменения» в полях, которые на самом деле не менялись (например, `boot_order` или `cdrom`). Lifecycle ignore:

```hcl
lifecycle {
  ignore_changes = [
    initialization[0].user_data_file_id,
    cdrom,
  ]
}
```

---

## 10. Что дальше

- Полный walkthrough от bootstrap'а — [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md)
- Пейлоад K8s в VM — [k8s-ansible CLAUDE.md](../CLAUDE.md)
