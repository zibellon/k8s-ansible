#!/usr/bin/env bash
# Iterate ansible-playbook --syntax-check over every playbook in
# playbook-system/ and playbook-app/. Exit 1 if any playbook fails;
# otherwise exit 0. Prints OK / FAIL per file.

set -u

fail=0
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

exit $fail
