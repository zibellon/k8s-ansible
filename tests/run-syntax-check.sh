#!/usr/bin/env bash
# Iterate ansible-playbook --syntax-check over every playbook in
# playbook-system/ and playbook-app/, then over every task-file in
# their tasks/ subdirectories (wrapped in a temporary import_tasks
# playbook because bare task-files are not valid Plays).
# Exit 1 if any check fails; otherwise exit 0. Prints OK / FAIL per file.

set -u

REPO_ROOT="$(pwd)"
WRAPPER=/tmp/syntax-wrapper.yaml
fail=0

# === Cycle 1: playbooks (top-level) ===
for f in playbook-system/*.yaml playbook-app/*.yaml; do
  if ansible-playbook --syntax-check \
       -i hosts-vars/ -i hosts-vars-test/ "$f" >/tmp/syntax.log 2>&1; then
    echo "OK:   $f"
  else
    echo "FAIL: $f"
    cat /tmp/syntax.log
    fail=1
  fi
done

# === Cycle 2: task files (wrapped in import_tasks playbook) ===
for f in playbook-system/tasks/*.yaml playbook-app/tasks/*.yaml; do
  cat > "$WRAPPER" <<EOF
- hosts: localhost
  gather_facts: false
  tasks:
    - import_tasks: $REPO_ROOT/$f
EOF
  if ansible-playbook --syntax-check \
       -i hosts-vars/ -i hosts-vars-test/ "$WRAPPER" >/tmp/syntax.log 2>&1; then
    echo "OK:   $f"
  else
    echo "FAIL: $f"
    cat /tmp/syntax.log
    fail=1
  fi
done

rm -f "$WRAPPER" /tmp/syntax.log
exit $fail
