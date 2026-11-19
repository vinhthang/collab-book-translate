"""3-tier authentication (ANONYMOUS, AGENT_KEY, ADMIN), rate limits, and token verification."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING, Literal, Mapping, Optional

if TYPE_CHECKING:
    from scripts.mcp_server.config import McpServerConfig
    from scripts.mcp_server.db import CollabStore

AuthTier = Literal["ANONYMOUS", "AGENT_KEY", "ADMIN"]


class AuthError(PermissionError):
    """Structured authentication (401) or authorization (403) error."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(RuntimeError):
    """Structured rate-limit exceeded (429) error."""

    def __init__(self, message: str, retry_after_seconds: int = 60) -> None:
        super().__init__(message)
        self.status_code = 429
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class AuthContext:
    tier: AuthTier
    actor_id: str
    handle: str
    client_ip_hash: str


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(sha256_hex(left), sha256_hex(right))


def mint_agent_key() -> tuple[str, str]:
    raw = f"cbt_ak_{secrets.token_hex(24)}"
    return raw, sha256_hex(raw)


def mint_revision_token() -> tuple[str, str]:
    raw = f"cbt_rt_{secrets.token_hex(24)}"
    return raw, sha256_hex(raw)


def verify_revision_token(provided_token: Optional[str], expected_sha256: str) -> None:
    token = (provided_token or "").strip()
    if not token or not expected_sha256:
        raise AuthError("Valid revision_token is required to update this submission", status_code=403)
    if not hmac.compare_digest(sha256_hex(token), expected_sha256):
        raise AuthError("Invalid revision_token for this submission", status_code=403)


def assert_admin(ctx: AuthContext) -> None:
    if ctx.tier != "ADMIN":
        raise AuthError("Admin privileges required for this operation", status_code=403)


def assert_not_self_review(reviewer_actor_id: str, author_actor_id: str) -> None:
    if not reviewer_actor_id or reviewer_actor_id == author_actor_id:
        raise AuthError("Self-review is prohibited: reviewer must differ from submission author", status_code=403)


def _extract_client_ip(headers: Mapping[str, str], fallback_ip: str = "127.0.0.1") -> str:
    lower = {k.lower(): v for k, v in headers.items()}
    cf_ip = lower.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    xff = lower.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return fallback_ip


def resolve_auth(
    *,
    store: "CollabStore",
    cfg: "McpServerConfig",
    headers: Mapping[str, str],
    request_path: str = "/mcp",
    client_ip: str = "127.0.0.1",
    agent_key_arg: Optional[str] = None,
    admin_token_arg: Optional[str] = None,
) -> AuthContext:
    """Resolve caller identity across ADMIN, AGENT_KEY, and ANONYMOUS tiers."""
    lower = {k.lower(): v for k, v in headers.items()}
    effective_ip = _extract_client_ip(lower, fallback_ip=client_ip)
    ip_hash = sha256_hex(f"collab-ip:{effective_ip}")[:16]

    # 1. Explicit admin_token tool argument
    if admin_token_arg is not None and admin_token_arg.strip():
        tok = admin_token_arg.strip()
        if cfg.collab_admin_token and constant_time_equal(tok, cfg.collab_admin_token):
            return AuthContext(
                tier="ADMIN",
                actor_id="admin:token",
                handle="admin",
                client_ip_hash=ip_hash,
            )
        raise AuthError("Invalid admin_token", status_code=401)

    # 2. HTTP Authorization: Bearer <token>
    auth_header = lower.get("authorization", "").strip()
    if auth_header:
        if not auth_header.lower().startswith("bearer "):
            raise AuthError("Authorization header must use Bearer scheme", status_code=401)
        bearer = auth_header[7:].strip()
        if not bearer:
            raise AuthError("Empty Bearer token", status_code=401)
        if cfg.collab_admin_token and constant_time_equal(bearer, cfg.collab_admin_token):
            return AuthContext(
                tier="ADMIN",
                actor_id="admin:token",
                handle="admin",
                client_ip_hash=ip_hash,
            )
        agent = store.get_agent_by_key(bearer)
        if agent is not None:
            handle = str(agent["handle"])
            return AuthContext(
                tier="AGENT_KEY",
                actor_id=f"agent:{handle}",
                handle=handle,
                client_ip_hash=ip_hash,
            )
        raise AuthError("Invalid or unknown Bearer token", status_code=401)

    # 3. Zero-header MCP tool argument agent_key
    if agent_key_arg is not None and agent_key_arg.strip():
        raw_ak = agent_key_arg.strip()
        agent = store.get_agent_by_key(raw_ak)
        if agent is not None:
            handle = str(agent["handle"])
            return AuthContext(
                tier="AGENT_KEY",
                actor_id=f"agent:{handle}",
                handle=handle,
                client_ip_hash=ip_hash,
            )
        raise AuthError("Invalid or unknown agent_key", status_code=401)

    # 4. Caddy Google OAuth2 Forward-Auth header — ONLY honored on /admin/* when enabled!
    if cfg.trust_admin_sso_header and request_path.startswith("/admin"):
        sso_email = lower.get("x-auth-request-email", "").strip().lower()
        if sso_email and (not cfg.admin_emails or sso_email in cfg.admin_emails):
            return AuthContext(
                tier="ADMIN",
                actor_id=f"admin:{sso_email}",
                handle=sso_email.split("@")[0],
                client_ip_hash=ip_hash,
            )

    # 5. Default public ANONYMOUS tier
    return AuthContext(
        tier="ANONYMOUS",
        actor_id=f"anon:{ip_hash}",
        handle=f"anon-{ip_hash[:8]}",
        client_ip_hash=ip_hash,
    )


class SlidingWindowRateLimiter:
    """Thread-safe in-process sliding-window rate limiter keyed by bucket string."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}

    def check(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_seconds: float = 60.0,
        now: Optional[float] = None,
    ) -> None:
        ts = time.time() if now is None else now
        cutoff = ts - window_seconds
        with self._lock:
            dq = self._buckets.get(bucket_key)
            if dq is None:
                dq = deque()
                self._buckets[bucket_key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry = max(1, int(window_seconds - (ts - dq[0])) + 1)
                raise RateLimitError(
                    f"Rate limit exceeded ({limit} requests per {int(window_seconds)}s)",
                    retry_after_seconds=retry,
                )
            dq.append(ts)
