#!/usr/bin/env bash
set -euo pipefail
# Run on the server after copying the generated public grant. Not a root key.
root=/home/tianqi/dev-middleware
account=chuanshen-dev-tunnel
test -s "$root/authorized_keys"
if ! id "$account" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$root/tunnel-user" --shell /usr/sbin/nologin "$account"
  # An unusable password, but unlike '!' not an account locked against keys.
  usermod --password '*' "$account"
fi
# /home/tianqi is intentionally private. Grant only this account traversal,
# not directory listing or a world-readable chmod on the server project root.
if ! command -v setfacl >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq acl
fi
setfacl -m "u:$account:--x" /home/tianqi
# The account needs traversal to its own home, never access to data or .env.
chmod 711 "$root"
install -d -o "$account" -g "$account" -m 700 "$root/tunnel-user/.ssh"
install -o "$account" -g "$account" -m 600 "$root/authorized_keys" "$root/tunnel-user/.ssh/authorized_keys"
install -m 644 "$root/sshd-dev-tunnel.conf" /etc/ssh/sshd_config.d/95-chuanshen-dev-tunnel.conf
sshd -t
systemctl reload ssh
echo 'Restricted forwarding-only account installed; existing sessions preserved.'
