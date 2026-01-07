#!/bin/bash

echo "Start backup - MAIN"
gitlab-backup create STRATEGY=copy BACKUP=dump GZIP_RSYNCABLE=yes SKIP=registry
echo "Start backup - CONFIG"
gitlab-ctl backup-etc

echo "Rename config backup"
cd /etc/gitlab/config_backup && mv $(ls -t | head -n1) config_gitlab_backup.tar && cd /

echo "Remove exist main backup from TMP"
rm -f /tmp-backup/dump_gitlab_backup.tar
echo "Remove exist config backup from TMP"
rm -f /tmp-backup/config_gitlab_backup.tar

echo "Copy main backup to TMP"
cp /var/opt/gitlab/backups/dump_gitlab_backup.tar /tmp-backup/dump_gitlab_backup.tar
echo "Copy config backup to TMP"
cp /etc/gitlab/config_backup/config_gitlab_backup.tar /tmp-backup/config_gitlab_backup.tar

echo "Remove main backup"
rm -f /var/opt/gitlab/backups/dump_gitlab_backup.tar
echo "Remove config backup"
rm -f /etc/gitlab/config_backup/config_gitlab_backup.tar