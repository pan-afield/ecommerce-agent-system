from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


def hash_password(password: str) -> str:
    ph = PasswordHasher()
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    ph = PasswordHasher()
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
