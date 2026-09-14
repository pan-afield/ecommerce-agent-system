import hashlib
import secrets


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """
    生成一个新的刷新令牌，并返回其明文和哈希值。

    Returns:
        tuple[str, str]: 返回一个包含刷新令牌明文和哈希值的元组。
    """
    # 生成一个随机的刷新令牌
    refresh_token = secrets.token_urlsafe(32)

    # 对刷新令牌进行哈希处理
    refresh_token_hash = hash_refresh_token(refresh_token)

    return refresh_token, refresh_token_hash
