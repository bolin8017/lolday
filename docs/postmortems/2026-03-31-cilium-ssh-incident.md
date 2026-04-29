# 2026-03-31 — Cilium install broke SSH on server30

**Incident:** Attempted to install Cilium as a replacement CNI for K3s's built-in flannel. After the Cilium agent started, host iptables rules were flushed — including the rules that kept SSH on port 9453 reachable from outside the lab network. The server was unreachable until physical access was arranged for recovery.

**Why root `CLAUDE.md` has an SSH-safety hard rule:** the lab has no IPMI or KVM-over-IP fallback for server30. Any change to CNI / iptables / UFW / sysctl / firewall rules must be dry-runnable and the operator must verify SSH from a fresh session before applying. This is now a hard rule on every session.

**Status:** Cilium was not retained. server30's K3s currently uses the built-in flannel CNI. Network-layer changes are gated by the SSH safety rule.

**Follow-ups (none active):** if a future networking project requires Cilium, plan it with operator-side fallback (out-of-band console / second SSH path / staging environment).
