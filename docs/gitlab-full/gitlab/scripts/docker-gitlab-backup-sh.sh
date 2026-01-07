#!/bin/bash
  
docker exec -t gitlab gitlab-backup create STRATEGY=copy BACKUP=dump GZIP_RSYNCABLE=yes

docker exec -t gitlab /bin/sh -c 'gitlab-ctl backup-etc && cd /etc/gitlab/config_backup && mv $(ls -t | head -n1) config_gitlab_backup.tar'

docker container run --rm -v ~/.aws:/root/.aws -v ~/gitlab/data/backups/dump_gitlab_backup.tar:/aws/dump_gitlab_backup.tar amazon/aws-cli s3 cp dump_gitlab_backup.tar s3://some-gitlab-backup/ --endpoint-url=https://storage.yandexcloud.net

docker container run --rm -v ~/.aws:/root/.aws -v ~/gitlab/config/config_backup/config_gitlab_backup.tar:/aws/config_gitlab_backup.tar amazon/aws-cli s3 cp config_gitlab_backup.tar s3://some-gitlab-backup/ --endpoint-url=https://storage.yandexcloud.net

rm -f ~/gitlab/data/backups/dump_gitlab_backup.tar
rm -f ~/gitlab/config/config_backup/config_gitlab_backup.tar