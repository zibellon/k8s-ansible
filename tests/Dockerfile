FROM python:3.12-slim

RUN apt-get update \
 && apt-get install --no-install-recommends -y bash git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      ansible-core==2.20.5 \
      ansible-lint==26.4.0 \
      yamllint==1.38.0

# ansible.posix is required for setup-ssh-keys.yaml (authorized_key module).
# ansible-lint mocks the module via mock_modules; ansible-playbook
# --syntax-check has no such mechanism, so the collection is installed
# directly. Pinned to a known stable release for reproducibility.
RUN ansible-galaxy collection install ansible.posix:1.5.4

ENV ANSIBLE_LINT_CACHE_DIR=/tmp/ansible-lint-cache

WORKDIR /repo
