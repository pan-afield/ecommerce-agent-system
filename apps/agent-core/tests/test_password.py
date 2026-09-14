from app.core.password import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies() -> None:
    password = "correct horse battery staple"

    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash) is True


def test_wrong_password_returns_false() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("wrong password", password_hash) is False
