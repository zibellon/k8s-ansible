DOCKER_IMAGE := k8s-ansible-test:local
DOCKER_RUN  := docker run --rm \
               -v "$(CURDIR):/repo:ro" \
               --tmpfs /tmp:rw,exec,size=100M \
               -w /repo \
               $(DOCKER_IMAGE)

.PHONY: help docker-build ensure-image test test-yamllint test-ansible-lint test-syntax

help:
	@echo "Targets:"
	@echo "  docker-build       Build the test image"
	@echo "  test               Run all tests (yamllint + ansible-lint + syntax-check)"
	@echo "  test-yamllint      yamllint over all YAML files"
	@echo "  test-ansible-lint  ansible-lint over playbook-system/ + playbook-app/"
	@echo "  test-syntax        ansible-playbook --syntax-check for every playbook"

docker-build:
	docker build -t $(DOCKER_IMAGE) -f tests/Dockerfile .

ensure-image:
	@docker image inspect $(DOCKER_IMAGE) >/dev/null 2>&1 || $(MAKE) docker-build

test-yamllint: ensure-image
	$(DOCKER_RUN) yamllint -c .yamllint.yaml .

test-ansible-lint: ensure-image
	$(DOCKER_RUN) ansible-lint -c .ansible-lint.yml --offline playbook-system/ playbook-app/

test-syntax: ensure-image
	$(DOCKER_RUN) bash tests/run-syntax-check.sh

test: test-yamllint test-ansible-lint test-syntax
