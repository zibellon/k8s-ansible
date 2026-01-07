external_url 'https://gitlab.my-domain.com'
nginx['listen_port'] = 80
nginx['listen_https'] = false
registry_external_url 'https://gitlab-registry.my-domain.com'
gitlab_rails['registry_enabled'] = true
registry['enable'] = true
registry_nginx['enable'] = true
registry_nginx['listen_port'] = 81
registry_nginx['listen_https'] = false
registry['storage'] = {
  's3' => {
    'accesskey' => 'some_access_key',
    'secretkey' => 'some_secret_key',
    'bucket' => 'registry',
    'region' => 'us-east-1',
    'regionendpoint' => 'gitlab-minio-api.my-domain.com',
    'checksum_disabled' => true,
    'pathstyle' => true
  }
}
pages_external_url 'https://gitlab-pages.my-domain.com'
gitlab_pages['enable'] = true
pages_nginx['enable'] = true
pages_nginx['listen_port'] = 82
pages_nginx['listen_https'] = false
gitlab_rails['gitlab_shell_ssh_port'] = 3714
gitlab_rails['smtp_enable'] = false
gitlab_rails['lfs_enabled'] = false
gitlab_rails['gitlab_kas_enabled'] = false
gitlab_kas['enable'] = false
alertmanager['enable'] = false
prometheus_monitoring['enable'] = false
prometheus['enable'] = false
sidekiq['metrics_enabled'] = false
sidekiq['max_concurrency'] = 10
puma['worker_processes'] = 0
gitlab_rails['object_store']['enabled'] = true
gitlab_rails['object_store']['proxy_download'] = false
gitlab_rails['object_store']['connection'] = {
  'provider' => 'AWS',
  'region' => 'us-east-1',
  'aws_access_key_id' => 'some_access_key',
  'aws_secret_access_key' => 'some_secret_key',
  'endpoint' => 'gitlab-minio-api.my-domain.com',
  'path_style' => true
}
gitlab_rails['object_store']['objects']['artifacts']['bucket'] = 'artifacts'
gitlab_rails['object_store']['objects']['external_diffs']['bucket'] = 'mr-diffs'
gitlab_rails['object_store']['objects']['lfs']['bucket'] = 'lfs'
gitlab_rails['object_store']['objects']['uploads']['bucket'] = 'uploads'
gitlab_rails['object_store']['objects']['packages']['bucket'] = 'packages'
gitlab_rails['object_store']['objects']['dependency_proxy']['bucket'] = 'dependency-proxy'
gitlab_rails['object_store']['objects']['terraform_state']['bucket'] = 'terraform-state'
gitlab_rails['object_store']['objects']['ci_secure_files']['bucket'] = 'ci-secure-files'
gitlab_rails['object_store']['objects']['pages']['bucket'] = 'pages'