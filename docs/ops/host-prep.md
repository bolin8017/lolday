# Host prep — one-time sysctls for the lolday cluster

These settings belong on each node that runs the lolday chart. They are
not managed by the chart because they require sudo and affect host-wide
kernel behaviour. `bash scripts/deploy.sh` checks nothing of this — if
you skip a step, the symptom is a specific pod crash at runtime.

## fs.inotify.max_user_instances

**Why:** Grafana Alloy (DaemonSet log shipper) watches every pod log
directory on the node. Ubuntu 24.04's default
`fs.inotify.max_user_instances=128` is exhausted after ~100 pods and
Alloy / Promtail crashes with `too many open files`.

**Apply:**

```bash
echo 'fs.inotify.max_user_instances = 8192' | sudo tee /etc/sysctl.d/99-promtail.conf
sudo sysctl --system
```

**Verify:** `sysctl fs.inotify.max_user_instances` should print 8192.

## kernel.apparmor_restrict_unprivileged_userns

**Why:** Rootless BuildKit (phase 9.3 build pipeline) uses
rootlesskit to set up an unprivileged user namespace, which Ubuntu
24.04's default AppArmor policy blocks. Without this sysctl, every
detector build fails in the `buildkit` container startup with:

```
[rootlesskit:parent] error: failed to setup UID/GID map:
  newuidmap 21 [...] failed: : fork/exec /usr/bin/newuidmap: operation not permitted
```

The pod exits and the build is marked failed; there is no workaround
at the Kubernetes spec level.

**Apply:**

```bash
echo 'kernel.apparmor_restrict_unprivileged_userns = 0' | sudo tee /etc/sysctl.d/99-buildkit-rootless.conf
sudo sysctl --system
```

**Verify:** `sysctl kernel.apparmor_restrict_unprivileged_userns` should
print 0. Then trigger a detector build via the backend and confirm the
buildkit container starts without a rootlesskit EPERM.

## Both at once (typical fresh-node setup)

```bash
sudo tee /etc/sysctl.d/99-lolday.conf <<'EOF'
fs.inotify.max_user_instances = 8192
kernel.apparmor_restrict_unprivileged_userns = 0
EOF
sudo sysctl --system
```

## Not included here

- `unprivileged_userns_clone` — already `1` by default on Ubuntu 24.04.
- `user.max_user_namespaces` — kernel default is already generous.
- `fs.inotify.max_user_watches` — only matters if we add deep filesystem
  watchers beyond pod logs; leave at kernel default unless alerts say so.
