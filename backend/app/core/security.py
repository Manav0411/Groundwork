import hashlib
import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.app_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )


def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check GitHub's `X-Hub-Signature-256` against the raw request body.

    This is the only authentication a webhook has: GitHub cannot send `X-API-Key`, so the signature
    is not a second factor but the whole gate. Two details carry that weight. The digest is taken
    over the raw bytes, because re-serialising the parsed JSON changes whitespace and key order and
    would fail every legitimate delivery. And the comparison is `compare_digest`, because `==`
    returns as soon as two bytes differ, which leaks the prefix a byte at a time to anyone willing
    to time the responses.
    """
    if not secret or not header:
        return False
    algorithm, _, sent = header.partition("=")
    if algorithm != "sha256" or not sent:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sent)
