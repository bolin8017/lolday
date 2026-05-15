"""M-csrf: gate POST/PUT/PATCH/DELETE on Origin/Sec-Fetch-Site (see plan section D1)."""


async def test_csrf_get_passes_without_headers(client):
    """Safe methods (GET/HEAD/OPTIONS) bypass CSRF check entirely."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200


async def test_csrf_post_same_origin_sec_fetch_site_passes(user_client):
    """POST with Sec-Fetch-Site: same-origin passes."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "csrf-test", "csv_url": "s3://x/y.csv"},
        headers={"sec-fetch-site": "same-origin"},
    )
    assert r.status_code != 403


async def test_csrf_post_none_sec_fetch_site_passes(user_client):
    """POST with Sec-Fetch-Site: none passes."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"sec-fetch-site": "none"},
    )
    assert r.status_code != 403


async def test_csrf_post_cross_site_sec_fetch_site_rejected(user_client):
    """POST with Sec-Fetch-Site: cross-site is rejected (403)."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert r.status_code == 403
    assert "csrf check failed" in r.text.lower()


async def test_csrf_post_same_site_sec_fetch_site_rejected(user_client):
    """POST with Sec-Fetch-Site: same-site (NOT same-origin) is rejected (403)."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"sec-fetch-site": "same-site"},
    )
    assert r.status_code == 403


async def test_csrf_post_origin_matches_host_passes(user_client):
    """POST with Origin matching Host passes."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"origin": "http://testserver", "host": "testserver"},
    )
    assert r.status_code != 403


async def test_csrf_post_origin_mismatch_host_rejected(user_client):
    """POST with Origin != Host is rejected (403)."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"origin": "http://evil.example", "host": "testserver"},
    )
    assert r.status_code == 403


async def test_csrf_post_neither_header_passes_fail_open(user_client):
    """POST with neither Origin nor Sec-Fetch-Site passes (fail-open per D1)."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
    )
    assert r.status_code != 403
    # Positive evidence the CSRF middleware did NOT 403 this request.
    # A 403 from somewhere else (e.g. a future auth-middleware deny) is
    # tolerated but it must not be the CSRF check.
    assert "csrf check failed" not in r.text.lower()


async def test_csrf_internal_path_bypasses_check(client):
    """/api/v1/internal/* is exempt."""
    r = await client.post(
        "/api/v1/internal/jobs/00000000-0000-0000-0000-000000000000/events",
        json={"kind": "test"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert "csrf check failed" not in r.text.lower()


async def test_csrf_mlflow_authz_path_bypasses_check(client):
    """/api/v1/mlflow-authz is the Traefik ForwardAuth target -- exempt."""
    r = await client.post(
        "/api/v1/mlflow-authz",
        json={},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert "csrf check failed" not in r.text.lower()


async def test_csrf_post_origin_default_port_80_matches_host(user_client):
    """Origin with :80 default port matches Host without port (and vice versa)."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"origin": "http://testserver:80", "host": "testserver"},
    )
    assert r.status_code != 403


async def test_csrf_post_origin_default_port_443_matches_host(user_client):
    """Origin with :443 default port (https) matches Host without port."""
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "x"},
        headers={"origin": "https://testserver:443", "host": "testserver"},
    )
    assert r.status_code != 403
