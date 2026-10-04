# Как примерно очистить WAL, если там что-то зависло

kubectl -n mon-system exec prometheus-prometheus-instance-0 -c prometheus -- sh -c 'cd /prometheus && mv wal wal.del && mv chunks_head chunks_head.del && rm -rf wal.del chunks_head.del && ls -la'