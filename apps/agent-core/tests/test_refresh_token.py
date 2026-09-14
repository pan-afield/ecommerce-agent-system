from app.core.refresh_token import generate_refresh_token, hash_refresh_token


def test_generated_refresh_token_is_random_and_stored_as_hash() -> None:
    raw_token, token_hash = generate_refresh_token()

    assert raw_token
    assert token_hash != raw_token
    assert len(token_hash) == 64


def test_refresh_token_hash_is_stable_for_lookup() -> None:
    raw_token = "test-refresh-token"

    first_hash = hash_refresh_token(raw_token)
    second_hash = hash_refresh_token(raw_token)

    assert first_hash == second_hash
    assert len(first_hash) == 64


def test_generated_refresh_tokens_are_unique() -> None:
    first_raw, _ = generate_refresh_token()
    second_raw, _ = generate_refresh_token()

    assert first_raw != second_raw
