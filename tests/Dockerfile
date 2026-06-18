FROM --platform=linux/amd64 python:3.12-slim

RUN apt-get update \
 && apt-get install --no-install-recommends -y bash git \
 && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
 && apt-get install --no-install-recommends -y curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      ansible-core==2.20.5 \
      ansible-lint==26.4.0 \
      yamllint==1.38.0 \
      pytest==8.3.4

# ansible.posix is required for setup-ssh-keys.yaml (authorized_key module).
# ansible-lint mocks the module via mock_modules; ansible-playbook
# --syntax-check has no such mechanism, so the collection is installed
# directly. Pinned to a known stable release for reproducibility.
RUN ansible-galaxy collection install ansible.posix:1.5.4

# community.general is required for the `archive` module used in
# playbook-app/tasks/tasks-copy-chart.yaml. ansible-lint mocks the
# module via mock_modules; ansible-playbook --syntax-check has no
# such mechanism, so the collection is installed directly. Pinned
# to a known stable release for reproducibility.
RUN ansible-galaxy collection install community.general:12.6.0

# helm — upstream binary, pinned for reproducibility (matches install-helm.yaml)
ARG HELM_VERSION=3.21.1
RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" \
    | tar -xz -C /tmp \
 && mv /tmp/linux-amd64/helm /usr/local/bin/helm \
 && rm -rf /tmp/linux-amd64

# kubeconform — strict K8s schema validator
ARG KUBECONFORM_VERSION=0.7.0
RUN curl -fsSL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
    | tar -xz -C /usr/local/bin kubeconform

ENV ANSIBLE_LINT_CACHE_DIR=/tmp/ansible-lint-cache

# Set PWD so {{ lookup('env', 'PWD') }} (used by hosts-vars/ansible.yaml's
# project_root) resolves correctly when ansible-playbook is invoked
# directly (without an outer shell, e.g. via `docker run ... ansible-playbook`).
# Shells normally set PWD, but Docker WORKDIR alone does not.
ENV PWD=/repo

WORKDIR /repo
