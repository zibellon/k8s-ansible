FROM python:3.12-slim

RUN apt-get update \
 && apt-get install --no-install-recommends -y bash git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      ansible-core==2.20.5 \
      ansible-lint==26.4.0 \
      yamllint==1.38.0

ENV ANSIBLE_LINT_CACHE_DIR=/tmp/ansible-lint-cache

WORKDIR /repo
