import hashlib
import hmac
import secrets

TOKEN_BYTES = 24
HMAC_SECRET_BYTES = 32


def generate_device_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_hmac_secret() -> str:
    return secrets.token_hex(HMAC_SECRET_BYTES)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def token_matches(stored_hash: str, presented_token: str) -> bool:
    return constant_time_equals(stored_hash, sha256_hex(presented_token))


def sign_body(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def signature_matches(secret: str, body: bytes, presented_signature: str) -> bool:
    expected = sign_body(secret, body)
    return constant_time_equals(expected, presented_signature.strip().lower())
