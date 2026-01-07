
# Как настраивать правильно S3 - для хранения всего и вся
https://docs.gitlab.com/administration/object_storage/#configure-each-object-type-to-define-its-own-storage-connection-storage-specific-form

# Как чистить registry: `gitlab-ctl registry-garbage-collect -m`

# Что-то прям ОЧЕНЬ интересное. Там прям МНОГО всего появляется

helm template gitlab gitlab/gitlab \
  --set global.hosts.domain=example.com \
  --set global.hosts.externalIP=10.10.10.10 \
  --set global.edition=ce \
  --set global.hosts.https=false \
  --set global.ingress.enabled=false \
  --set global.ingress.configureCertmanager=false \
  --set upgradeCheck.enabled=false \
  --set installCertmanager=false \
  --set certmanager.installCRDs=false \
  --set certmanager-issuer.email=kek_lol_123@gmail.com \
  --set nginx-ingress.enabled=false \
  --set prometheus.install=false \
  --set shared-secrets.enabled=false \
  --set gitlab-runner.install=false > gitlab-install.yaml