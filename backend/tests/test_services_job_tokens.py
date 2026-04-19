from app.services.job_tokens import generate_token, hash_token, verify_token


def test_generate_token_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2
    assert len(t1) >= 32


def test_hash_token_deterministic():
    t = "my-token"
    h1 = hash_token(t)
    h2 = hash_token(t)
    assert h1 == h2
    assert len(h1) == 64


def test_verify_token_matches():
    t = generate_token()
    h = hash_token(t)
    assert verify_token(t, h) is True


def test_verify_token_rejects_wrong():
    t = generate_token()
    h = hash_token(t)
    assert verify_token("other-token", h) is False
