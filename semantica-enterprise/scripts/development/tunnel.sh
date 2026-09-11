#!/bin/sh
set -eu
# No host port is published: only the local Compose network can reach these.
exec ssh -N -T -g \
  -i /run/dev-ssh/id_ed25519 \
  -o UserKnownHostsFile=/run/dev-ssh/known_hosts \
  -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 0.0.0.0:5432:127.0.0.1:25432 \
  -L 0.0.0.0:6379:127.0.0.1:26379 \
  -L 0.0.0.0:5672:127.0.0.1:25672 \
  -L 0.0.0.0:9000:127.0.0.1:29000 \
  -L 0.0.0.0:9200:127.0.0.1:29200 \
  -L 0.0.0.0:6333:127.0.0.1:26333 \
  -L 0.0.0.0:6380:127.0.0.1:26380 \
  "${DEV_SSH_USER:-chuanshen-dev-tunnel}@${DEV_SSH_HOST:-10.5.113.232}"
