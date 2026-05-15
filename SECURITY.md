# Security Policy

Lolday is an internal ML platform for ISLab's malware detector lifecycle
(see [`README.md`](README.md)). The repository is public for transparency
and collaboration; the production deployment lives on a single private
K3s host that is not exposed to the open internet. This policy applies to
the **code** in this repository.

## Supported Versions

Lolday tracks a single rolling `main` branch. The most recent tagged
release (see [`charts/lolday/Chart.yaml`](charts/lolday/Chart.yaml)
`version`) is the only supported version. Older tags receive **no
security backports**.

| Version                  | Supported |
| ------------------------ | --------- |
| `main` and latest 0.24.x | ✅        |
| 0.23.x and earlier       | ❌        |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Use GitHub's **private vulnerability reporting**:
**https://github.com/bolin8017/lolday/security/advisories/new**

Include:

- A clear description of the issue and its impact (confidentiality /
  integrity / availability).
- Steps to reproduce, or a proof-of-concept.
- Affected versions / commit SHA, if known.
- Your suggested CVSS severity (we will recompute, but a starting
  point helps triage).

### Response timeline

- **Acknowledgement** of receipt: within 48 hours.
- **Initial triage** (severity + scope assessment): within 7 days.
- **Fix + advisory shipped**:
  - Critical (CVSS 9.0+): within 14 days
  - High (CVSS 7.0–8.9): within 30 days
  - Medium / Low: queued into the next regular release cycle

Researchers are credited in the published advisory unless they ask to
remain anonymous. There is no monetary bounty programme — Lolday is an
academic-lab platform with no commercial revenue.

## Scope

**In scope**:

- The `bolin8017/lolday` repository (this repo)
- Container images published from this repo:
  `ghcr.io/bolin8017/lolday-*`,
  `harbor.lolday.svc:80/lolday/*`
- The Helm chart `charts/lolday/` and the Kyverno admission policies it
  installs
- The operator scripts under `scripts/`

**Out of scope** (report upstream):

- The `maldet` framework — https://github.com/bolin8017/maldet (separate policy)
- Detectors that consume `maldet` (`elfrfdet`, `elfcnndet`, …)
- Upstream Kubernetes / K3s / Volcano / Kyverno / Harbor / MinIO / MLflow /
  kube-prometheus-stack / Cloudflare Access / Traefik — report directly to
  the upstream project.

## Security architecture overview

For context on Lolday's defence-in-depth posture, see:

- [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](docs/superpowers/specs/2026-05-12-security-hardening-design.md)
  — six-phase hardening program (P1–P6 closed 2026-05-14)
- [`docs/postmortems/2026-05-12-security-audit-program.md`](docs/postmortems/2026-05-12-security-audit-program.md)
  — program postmortem + five root-cause themes
- [`docs/phase-history/2026-05-14-security-audit-findings.md`](docs/phase-history/2026-05-14-security-audit-findings.md)
  — finding-by-finding closeout ledger
- [`docs/phase-history/2026-05-15-security-post-program-review.md`](docs/phase-history/2026-05-15-security-post-program-review.md)
  — post-program review (OWASP / ASVS / CIS / NSA-CISA / SLSA cross-check)
- [`docs/runbooks/kyverno-harbor-signing.md`](docs/runbooks/kyverno-harbor-signing.md)
  — image supply-chain signing flow
- [`.claude/rules/github-actions.md`](.claude/rules/github-actions.md) —
  CI/CD discipline (action SHA pinning, minimal permissions)

Key controls:

- Cloudflare Access SSO is the single human-auth path (RS256 JWT;
  aud/iss/exp/iat verified).
- All container images are digest-pinned in `values.yaml` and signed
  via Cosign (GHCR keyless via GHA OIDC; Harbor with operator-managed
  key). Kyverno `verifyImages` ClusterPolicy enforces signature
  verification at admission.
- SLSA L3 build provenance via `actions/attest-build-provenance` in CI.
- Pod Security Standards labels at `audit/warn=restricted` (and
  `enforce=restricted` for application namespaces after observation
  windows). Default-deny NetworkPolicies in `lolday`, `lolday-jobs`,
  `lolday-builds`, `monitoring`, `trivy-system`.
- K3s API server audit log + `--secrets-encryption` enabled.
- Application-level `audit_log` table records security-relevant user
  actions.
- GitHub Secret Scanning + Push Protection + Dependabot Security
  Updates enabled at the repository level.

## Acknowledgements

The 2026-05-12 audit program closed 88 finding-IDs and is documented in
full in the artefacts linked above. Subsequent vulnerability reports
will be credited in this section as advisories are published.
