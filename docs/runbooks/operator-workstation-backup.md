# Operator workstation off-site backup runbook

## What this is

The lolday operator's workstation holds the single copy of:

- `.lolday-secrets.env` (chmod 600) — main secrets file
- `.lolday-cf-svctoken.env` — CF Access service-token split
- `.lolday-cloudflare-access-backups/` — age-encrypted CF Access app/policy snapshots
- `~/.config/age/lolday-cf-access.key` — age private key
- `~/.cosign/lolday-harbor.key` — Harbor cosign signing key (from issue #171)
- SSH key for server30 (port 9453)

If the workstation is lost / encrypted / stolen and no off-site recovery copy
exists, **the cluster cannot be re-deployed** without re-issuing CF Access app
config, regenerating Harbor admin password, re-deriving Fernet keys (= losing
all UserGitCredential rows), and re-keying Harbor cosign signatures.

## Recommended approach

Mainstream pattern: age-encrypted bundle to an off-site location, with the
age private key in a hardware token or held by a second researcher.

### Step 1 — generate a recovery age keypair (one-time)

```bash
age-keygen -o ~/.config/age/recovery.key
chmod 600 ~/.config/age/recovery.key
# Read the public key from the file (it's in a comment at the top)
grep '# public key' ~/.config/age/recovery.key
```

Store the **private key** outside the workstation. Options (pick one):

- **Hardware token** (e.g., YubiKey with age-plugin-yubikey) — strongest.
- **Second researcher** holds an encrypted copy on their workstation.
- **Cloud vault** (1Password, Bitwarden) — convenient; trust the vault.

### Step 2 — bundle + encrypt

```bash
tar czf /tmp/lolday-workstation-secrets-$(date +%Y%m%d).tgz \
    ~/.lolday-secrets.env \
    ~/.lolday-cf-svctoken.env \
    ~/.lolday-cloudflare-access-backups/ \
    ~/.config/age/lolday-cf-access.key \
    ~/.cosign/lolday-harbor.key \
    ~/.ssh/server30 \
    ~/.ssh/server30.pub

age -r '<recovery-pubkey>' /tmp/lolday-workstation-secrets-$(date +%Y%m%d).tgz \
    > /tmp/lolday-workstation-secrets-$(date +%Y%m%d).tgz.age

shred -u /tmp/lolday-workstation-secrets-$(date +%Y%m%d).tgz
```

### Step 3 — upload .age to off-site

- S3-compatible (Cloudflare R2, AWS S3, Backblaze B2) — mainstream
- GitHub private gist (small files only; large secrets won't fit)
- Personal cloud (Dropbox, Google Drive) — trust the provider with the encrypted bundle

### Step 4 — cadence

- After every secrets-file edit (e.g., MinIO key rotation, Fernet key bump)
- On a monthly cron for the operator workstation (use `cron` or `launchd` or
  `systemd --user`)
- After any major chart upgrade that introduces new secret files

### Step 5 — restore drill

Quarterly: pull the latest .age bundle to a clean test environment, decrypt
with the recovery private key, verify the bundle is complete. Document any
gaps.

## Anti-patterns

- Storing the recovery private key on the same workstation as the encrypted
  bundle (defeats the purpose).
- Pushing unencrypted .tgz to any remote.
- Skipping the restore drill ("we'll figure it out when we need it" —
  the moment you need it is the worst time to discover a gap).
