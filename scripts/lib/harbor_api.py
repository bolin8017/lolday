"""Harbor v2 REST API helpers used by scripts/{build-helpers,recover-harbor}.sh.

Phase 4 D4.2 R6 extraction. Replaces multiple `python3 -<<'PY' ... PY` heredocs
that were inlined into bash. The shell scripts now call:

    python3 -m scripts.lib.harbor_api <verb> [args...]

verbs:
    creds-namespace             — print the K8s namespace holding the
                                  harbor-push-cred Secret (lolday | lolday-jobs)
    decode-dockerconfig [file]  — read dockerconfigjson from <file> or stdin,
                                  print the "robot$build-pusher:<secret>"
                                  auth tuple (base64-decoded)
    build-dockerconfig <user> <secret> <host>
                                — build the dockerconfigjson body
                                  (registers both .svc:80 and the host alias)
                                  and print it base64-encoded
    has-tag <name> <sha>        — exit 0 if Harbor serves
                                  lolday/<name>:<sha>, 1 if 404, 2 on error
    get-digest <name> <sha>     — print the artifact's @sha256:<hex>
                                  digest; exit 2 on error
    parse-robot-list            — read the JSON-array response from
                                  GET /robots?q=name=build-pusher on stdin,
                                  print the id of the matching robot
                                  (empty string + exit 0 if none)
    robot-state                 — read a GET /robots/{id} response on stdin,
                                  print one of: empty | missing-core
                                  | already-has-cache | needs-cache
    add-cache-perm              — read a GET /robots/{id} response on stdin,
                                  emit the PUT body that appends the
                                  detectors-cache repository:push+pull
                                  permission
    redact-robot-response       — read a POST/PATCH /robots[/id] response
                                  on stdin, print the shape with the secret
                                  field replaced by "<redacted>"

Env-driven knobs (so the bash side never puts secrets on the command line):

    HARBOR_CRED_NS              — explicit namespace override for has-tag /
                                  get-digest (skips the lolday/lolday-jobs
                                  probe)
    HARBOR_HOST                 — base Harbor host:port (default
                                  harbor.lolday.svc.cluster.local:80)
    HARBOR_PROJECT              — Harbor project (default "lolday")

Tested via scripts/tests/lib/test_harbor_api.py with respx for HTTP
and monkeypatched subprocess for the kubectl side.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

import httpx

DEFAULT_HARBOR_HOST = "harbor.lolday.svc.cluster.local:80"
DEFAULT_HARBOR_PROJECT = "lolday"
CANDIDATE_NAMESPACES = ("lolday", "lolday-jobs")


def _harbor_host() -> str:
    return os.environ.get("HARBOR_HOST", DEFAULT_HARBOR_HOST)


def _harbor_project() -> str:
    return os.environ.get("HARBOR_PROJECT", DEFAULT_HARBOR_PROJECT)


def _kubectl_get_secret(namespace: str, name: str) -> str | None:
    """Return the .dockerconfigjson value (base64-decoded JSON string)
    of <namespace>/<name>, or None if the Secret does not exist."""
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "secret",
            name,
            "-o",
            "jsonpath={.data.\\.dockerconfigjson}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return base64.b64decode(result.stdout.strip()).decode()


def creds_namespace() -> str:
    """Find the K8s namespace holding harbor-push-cred. Honour
    HARBOR_CRED_NS if set."""
    override = os.environ.get("HARBOR_CRED_NS")
    if override:
        return override
    for ns in CANDIDATE_NAMESPACES:
        if _kubectl_get_secret(ns, "harbor-push-cred") is not None:
            return ns
    raise RuntimeError(
        "harbor-push-cred Secret not found in any of: "
        + ", ".join(CANDIDATE_NAMESPACES)
    )


def decode_dockerconfig(cfg_json: str, *, host_key: str | None = None) -> str:
    """Decode the auth tuple ('robot$build-pusher:<secret>') from a
    dockerconfigjson body. Pick the .svc:80 entry by default — see
    docstring on build-helpers.sh::harbor_login for why."""
    data = json.loads(cfg_json)
    auths = data.get("auths", {})
    key = host_key or "harbor.lolday.svc:80"
    if key not in auths:
        if len(auths) != 1:
            raise KeyError(
                f"dockerconfigjson missing {key!r} and has {len(auths)} other "
                f"auths entries; cannot disambiguate"
            )
        key = next(iter(auths))
    encoded = auths[key]["auth"]
    return base64.b64decode(encoded).decode()


def build_dockerconfig(user: str, secret: str, host_alias: str) -> str:
    r"""Build a dockerconfigjson registering BOTH harbor.lolday.svc:80
    (K3s containerd) and host_alias (host docker). Return base64-encoded
    JSON ready for use as Secret.data.\.dockerconfigjson."""
    auth = base64.b64encode(f"{user}:{secret}".encode()).decode()
    cfg = {
        "auths": {
            "harbor.lolday.svc:80": {"auth": auth},
            host_alias: {"auth": auth},
        }
    }
    return base64.b64encode(json.dumps(cfg).encode()).decode()


def _harbor_artifact_url(name: str, sha: str) -> str:
    return (
        f"http://{_harbor_host()}/api/v2.0/projects/{_harbor_project()}"
        f"/repositories/{name}/artifacts?with_tag=true&q=tags={sha}"
    )


def _auth_header_from_creds() -> str:
    ns = creds_namespace()
    cfg = _kubectl_get_secret(ns, "harbor-push-cred")
    if cfg is None:
        raise RuntimeError(f"harbor-push-cred unexpectedly missing in {ns}")
    auth = json.loads(cfg)["auths"]["harbor.lolday.svc:80"]["auth"]
    return f"Basic {auth}"


def has_tag(name: str, sha: str, *, client: httpx.Client | None = None) -> bool:
    """Return True iff Harbor serves <project>/<name>:<sha>. 404 → False;
    any other non-200 → raise RuntimeError."""
    if not _is_safe_sha(sha):
        raise ValueError(f"refusing non-SHA arg: {sha!r}")
    url = _harbor_artifact_url(name, sha)
    headers = {"Authorization": _auth_header_from_creds()}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(url, headers=headers)
    finally:
        if owns_client:
            client.close()
    if resp.status_code == 200:
        body = resp.json()
        return isinstance(body, list) and len(body) > 0
    if resp.status_code == 404:
        return False
    raise RuntimeError(f"has-tag {name} {sha} HTTP {resp.status_code}: {resp.text}")


def get_digest(name: str, sha: str, *, client: httpx.Client | None = None) -> str:
    """Return the @sha256:<hex> digest for <project>/<name>:<sha>. Raise
    RuntimeError on HTTP error or unexpected payload shape."""
    if not _is_safe_sha(sha):
        raise ValueError(f"refusing non-SHA arg: {sha!r}")
    url = _harbor_artifact_url(name, sha)
    headers = {"Authorization": _auth_header_from_creds()}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(url, headers=headers)
    finally:
        if owns_client:
            client.close()
    if resp.status_code != 200:
        raise RuntimeError(
            f"get-digest {name} {sha} HTTP {resp.status_code}: {resp.text}"
        )
    body = resp.json()
    if not isinstance(body, list) or not body:
        raise RuntimeError(f"get-digest {name} {sha}: empty artifact list")
    digest = body[0].get("digest", "")
    if not _is_sha256_digest(digest):
        raise RuntimeError(f"get-digest {name} {sha}: unexpected digest {digest!r}")
    return digest


def parse_robot_list(robots_json: str) -> str:
    """From the JSON-array response of GET /robots?q=name=build-pusher,
    return the id of the matching robot or an empty string."""
    try:
        rows = json.loads(robots_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if row.get("name") in ("robot$build-pusher", "build-pusher"):
            return str(row.get("id", ""))
    return ""


def robot_state(robot_json: str) -> str:
    """Classify the permissions array of a GET /robots/{id} response.

    Returns one of: empty | missing-core | already-has-cache | needs-cache.
    """
    data = json.loads(robot_json)
    perms = data.get("permissions") or []
    namespaces = {p.get("namespace") for p in perms if isinstance(p, dict)}
    if not perms:
        return "empty"
    if not {"lolday", "detectors"}.issubset(namespaces):
        return "missing-core"
    if "detectors-cache" in namespaces:
        return "already-has-cache"
    return "needs-cache"


def add_cache_perm(robot_json: str) -> str:
    """Append a detectors-cache repository:push+pull permission to the
    given GET /robots/{id} body and return the PUT body (JSON string)."""
    data = json.loads(robot_json)
    data.setdefault("permissions", []).append(
        {
            "kind": "project",
            "namespace": "detectors-cache",
            "access": [
                {"resource": "repository", "action": "push"},
                {"resource": "repository", "action": "pull"},
            ],
        }
    )
    keep = [
        "name",
        "level",
        "duration",
        "description",
        "disable",
        "editable",
        "expires_at",
        "permissions",
    ]
    body = {k: data[k] for k in keep if k in data}
    return json.dumps(body)


def redact_robot_response(robot_json: str) -> str:
    """Echo the robot response body with the ``secret`` field replaced
    by '<redacted>'. Used for log lines that must never carry the
    plaintext secret."""
    data = json.loads(robot_json)
    redacted = {k: ("<redacted>" if k == "secret" else v) for k, v in data.items()}
    return json.dumps(redacted)


# --- input validation -------------------------------------------------


def _is_safe_sha(s: str) -> bool:
    """Mirror of build-helpers.sh's regex guard: short-12 subtree SHA up
    to full 64-char sha256 hex."""
    if not 6 <= len(s) <= 64:
        return False
    return all(c in "0123456789abcdef" for c in s)


def _is_sha256_digest(s: str) -> bool:
    return (
        s.startswith("sha256:")
        and len(s) == len("sha256:") + 64
        and _is_safe_sha(s[7:])
    )


# --- CLI dispatch -----------------------------------------------------


def _dispatch(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: python -m scripts.lib.harbor_api <verb> [args...]", file=sys.stderr
        )
        return 2
    verb, *args = argv
    try:
        if verb == "creds-namespace":
            print(creds_namespace())
        elif verb == "decode-dockerconfig":
            if args:
                with open(args[0], encoding="utf-8") as f:
                    cfg = f.read()
            else:
                cfg = sys.stdin.read()
            print(decode_dockerconfig(cfg))
        elif verb == "build-dockerconfig":
            user, secret, host = args
            print(build_dockerconfig(user, secret, host))
        elif verb == "has-tag":
            name, sha = args
            return 0 if has_tag(name, sha) else 1
        elif verb == "get-digest":
            name, sha = args
            print(get_digest(name, sha))
        elif verb == "parse-robot-list":
            print(parse_robot_list(sys.stdin.read()))
        elif verb == "robot-state":
            print(robot_state(sys.stdin.read()))
        elif verb == "add-cache-perm":
            print(add_cache_perm(sys.stdin.read()))
        elif verb == "redact-robot-response":
            print(redact_robot_response(sys.stdin.read()))
        else:
            print(f"unknown verb: {verb}", file=sys.stderr)
            return 2
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    return _dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
