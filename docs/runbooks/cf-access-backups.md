# Cloudflare Access backups — age-encrypted snapshots

**Scope:** operator-local backups of Cloudflare Access app + policy state, kept
for audit. Stored under `.lolday-cloudflare-access-backups/` (repo-root,
gitignored). Every snapshot must be encrypted with [`age`](https://age-encryption.org/);
cleartext `.json` files are forbidden.

**Why this exists:** the snapshots reveal SSO architecture details — rule IDs,
identity-provider configuration, OTP/email-binding state, group claims. An
attacker who reads them learns how to craft a JWT that satisfies the live
policy without ever talking to Cloudflare. The repo `.gitignore` keeps them
out of git, but the operator workstation is the residual exposure surface.

## Prerequisites

Install age (Ubuntu 24.04+):

```bash
sudo apt install age   # or:  ~/.local/bin/age — download from github.com/FiloSottile/age/releases
```

Generate (or import) an X25519 keypair, stored under `~/.config/age/`:

```bash
mkdir -p ~/.config/age && chmod 700 ~/.config/age
age-keygen -o ~/.config/age/lolday-cf-access.key
chmod 600 ~/.config/age/lolday-cf-access.key
```

Note the recipient line printed at the top of the keyfile (`# public key:
age1...`). Export it for convenience:

```bash
export AGE_RECIPIENT="$(grep -oE 'age1[0-9a-z]+' ~/.config/age/lolday-cf-access.key | head -1)"
```

Persist `AGE_RECIPIENT` in `~/.zshrc` so future invocations don't need to
re-read the keyfile.

## Capture a new snapshot

```bash
cd ~/Documents/repositories/lolday
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVENT=otp-removal-pre  # short description; goes in the filename
curl -sS -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/apps" \
  | age -r "$AGE_RECIPIENT" > ".lolday-cloudflare-access-backups/app-$EVENT-$STAMP.json.age"
curl -sS -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/policies" \
  | age -r "$AGE_RECIPIENT" > ".lolday-cloudflare-access-backups/policy-$EVENT-$STAMP.json.age"
```

The cleartext API response goes straight into age via stdin — never lands on disk.

## Read an existing snapshot

```bash
age -d -i ~/.config/age/lolday-cf-access.key \
  .lolday-cloudflare-access-backups/app-otp-removal-pre-20260422T122701Z.json.age \
  | jq .
```

## Migrate existing cleartext snapshots

Run once on the operator workstation:

```bash
cd ~/Documents/repositories/lolday/.lolday-cloudflare-access-backups
shopt -s nullglob
for f in *.json; do
  age -r "$AGE_RECIPIENT" < "$f" > "$f.age" && shred -u "$f"
done
```

Verify: `ls *.json 2>/dev/null` returns nothing; `ls *.json.age` shows the
encrypted files.

## Key management

- The age key is operator-local. **Never commit it.** It sits in `~/.config/age/`
  under chmod 600.
- For survivability, copy the keyfile to a second device (encrypted USB, password
  manager attachment). Losing the key means every existing `.json.age` is
  unrecoverable.
- Rotation: generate a new keypair, re-encrypt every `.json.age` under the new
  recipient (`age -d -i OLD.key file.age | age -r NEW_RECIPIENT > file.age.tmp
&& mv file.age.tmp file.age`), then `shred -u OLD.key`. Update
  `AGE_RECIPIENT` in `~/.zshrc`.

## Why age and not GPG?

age has a single binary, no agent / keyring machinery, X25519 keys that double
as the encrypt + decrypt material, and no key-server dependency. The operator
is one person; GPG's web-of-trust adds no value here.
