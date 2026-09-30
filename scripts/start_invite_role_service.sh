#!/bin/zsh
set -euo pipefail
umask 077

cd /Users/kmini/lab-agent-system
export PYTHONPATH=.

for env_file in .env.invite-role-service; do
  if [[ -f "$env_file" ]]; then
    set -a
    source "$env_file"
    set +a
  fi
done

exec ./.venv/bin/python scripts/run_invite_role_service.py --config operations/invite_role_service.config.json
