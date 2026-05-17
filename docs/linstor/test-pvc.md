# Apply manifest:
kubectl apply -f test2.yaml

# Wait + verify:
kubectl get pvc -n default                  # оба PVC должны быть Bound (потому-что `volumeBindingMode: Immediate`)

kubectl -n piraeus-datastore exec deploy/linstor-controller -- linstor resource list
# Ожидаем:
#  - resource pvc-<UUID-managers> на k8s-manager-1 (UpToDate)
#  - resource pvc-<UUID-workers> на k8s-worker-1 или k8s-worker-2 (UpToDate)
#
# Critical check: НИ ОДНА replica test-pvc-managers НЕ должна попасть на worker, и наоборот.

# Cleanup
kubectl delete pvc -n default test-pvc-managers test-pvc-workers test-pvc-multi
kubectl delete sc test-managers-only test-workers-only test-multi-pool