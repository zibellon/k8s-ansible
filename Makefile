DOCKER_IMAGE := k8s-ansible-test:local
DOCKER_RUN  := docker run --rm \
               --tmpfs /tmp:rw,exec,size=100M \
               $(DOCKER_IMAGE)

.PHONY: help docker-build ensure-image test test-yamllint test-ansible-lint test-syntax test-helm test-pytest

help:
	@echo "Targets:"
	@echo "  docker-build       Build the test image"
	@echo "  test               Run all tests (yamllint + ansible-lint + syntax-check + helm + pytest)"
	@echo "  test-yamllint      yamllint over all YAML files"
	@echo "  test-ansible-lint  ansible-lint over playbook-system/ + playbook-app/"
	@echo "  test-syntax        ansible-playbook --syntax-check for every playbook"
	@echo "  test-helm          helm template + kubeconform for upstream charts"
	@echo "  test-pytest        pytest unit tests for filter plugins (Python compute)"

docker-build:
	docker build -t $(DOCKER_IMAGE) -f tests/Dockerfile .

# The image carries a snapshot of the repo (COPY . /repo in tests/Dockerfile),
# so every test target rebuilds it first — otherwise the suite would validate
# stale code. Warm rebuilds are ~0.8 s: tool layers stay cached, only COPY runs.
ensure-image:
	@docker build -q -t $(DOCKER_IMAGE) -f tests/Dockerfile . > /dev/null

test-yamllint: ensure-image
	$(DOCKER_RUN) yamllint -c .yamllint.yaml .

test-ansible-lint: ensure-image
	$(DOCKER_RUN) ansible-lint -c .ansible-lint.yml --offline playbook-system/ playbook-app/

test-syntax: ensure-image
	$(DOCKER_RUN) bash tests/run-syntax-check.sh

test-helm: ensure-image
	$(DOCKER_RUN) ansible-playbook -i hosts-vars/ -i hosts-vars-test/ tests/helm-validate.yaml

test-pytest: ensure-image
	$(DOCKER_RUN) pytest tests/python/ -v -p no:cacheprovider

test:
	@START=$$(date +%s) && \
	$(MAKE) test-yamllint test-ansible-lint test-syntax test-helm test-pytest && \
	END=$$(date +%s) && \
	DURATION=$$((END - START)) && \
	M=$$((DURATION / 60)) && \
	S=$$((DURATION % 60)) && \
	printf "\n==========================================\n" && \
	printf "make test → exit 0 (all 5 stages passed)\n" && \
	printf "Wall-clock: %dm %02ds\n" $$M $$S && \
	printf "==========================================\n"
