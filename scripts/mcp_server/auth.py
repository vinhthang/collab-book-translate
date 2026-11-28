"""3-tier authentication (ANONYMOUS, AGENT_KEY, ADMIN), 3-word key minting, rate limits, and token verification."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional

from scripts.mcp_server.keys import (
    _ADJECTIVES,
    _NOUNS,
    _PLACES,
    generate_default_handle,
    generate_readable_handle,
    mint_agent_key,
    sha256_hex,
)

if TYPE_CHECKING:
    from scripts.mcp_server.config import McpServerConfig
    from scripts.mcp_server.db import CollabStore

AuthTier = Literal["ANONYMOUS", "AGENT_KEY", "ADMIN"]
AgentRole = Literal["contributor", "agent_admin", "tech_admin", "admin"]
BEARER_LOOKUP_MISS = object()


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
    agent_role: AgentRole = "contributor"


def constant_time_equal(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(sha256_hex(left), sha256_hex(right))


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


def assert_agent_admin_or_admin(ctx: AuthContext) -> None:
    if ctx.tier == "ADMIN":
        return
    if ctx.tier == "AGENT_KEY" and ctx.agent_role == "agent_admin":
        return
    raise AuthError("Agent Admin or Admin privileges required for this operation", status_code=403)


def assert_bug_handler_or_admin(ctx: AuthContext) -> None:
    if ctx.tier == "ADMIN":
        return
    if ctx.tier == "AGENT_KEY" and ctx.agent_role in {"agent_admin", "tech_admin"}:
        return
    raise AuthError("Tech Admin, Agent Admin, or Admin privileges required for this operation", status_code=403)


def _normalize_agent_role(raw: object) -> AgentRole:
    role_val = str(raw or "").strip().lower()
    if role_val == "agent_admin":
        return "agent_admin"
    if role_val == "tech_admin":
        return "tech_admin"
    return "contributor"


def assert_not_self_review(reviewer_actor_id: str, author_actor_id: str) -> None:
    if not reviewer_actor_id or reviewer_actor_id == author_actor_id:
        raise AuthError("Self-review is prohibited: reviewer must differ from submission author", status_code=403)


def _extract_client_ip(
    headers: Mapping[str, str],
    fallback_ip: str = "127.0.0.1",
    *,
    trust_proxy_headers: bool = False,
    trusted_proxy_ips: frozenset[str] = frozenset({"127.0.0.1", "::1", "testclient"}),
) -> str:
    if not (trust_proxy_headers or fallback_ip in trusted_proxy_ips):
        return fallback_ip
    lower = {k.lower(): v for k, v in headers.items()}
    cf_ip = lower.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    xff = lower.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return fallback_ip


def _record_auth_failure(
    rate_limiter: Optional["SlidingWindowRateLimiter"],
    cfg: "McpServerConfig",
    ip_hash: str,
) -> None:
    if rate_limiter is not None:
        rate_limiter.check(
            f"auth_fail:{ip_hash}",
            limit=cfg.auth_fail_rpm,
            window_seconds=60.0,
        )


def resolve_auth(
    *,
    store: "CollabStore",
    cfg: "McpServerConfig",
    headers: Mapping[str, str],
    request_path: str = "/mcp",
    client_ip: str = "127.0.0.1",
    agent_key_arg: Optional[str] = None,
    admin_token_arg: Optional[str] = None,
    transport: str = "",
    action: str = "",
    client_name: str = "",
    rate_limiter: Optional["SlidingWindowRateLimiter"] = None,
    pre_authenticated_agent: Optional[Any] = None,
) -> AuthContext:
    """Resolve caller identity across ADMIN, AGENT_KEY, and ANONYMOUS tiers."""
    lower = {k.lower(): v for k, v in headers.items()}
    effective_ip = _extract_client_ip(
        lower,
        fallback_ip=client_ip,
        trust_proxy_headers=cfg.trust_proxy_headers,
        trusted_proxy_ips=cfg.trusted_proxy_ips,
    )
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
                agent_role="admin",
            )
        _record_auth_failure(rate_limiter, cfg, ip_hash)
        raise AuthError("Invalid admin_token", status_code=401)

    # 2. HTTP Authorization: Bearer <token>
    auth_header = lower.get("authorization", "").strip()
    if auth_header:
        if not auth_header.lower().startswith("bearer "):
            _record_auth_failure(rate_limiter, cfg, ip_hash)
            raise AuthError("Authorization header must use Bearer scheme", status_code=401)
        bearer = auth_header[7:].strip()
        if not bearer:
            _record_auth_failure(rate_limiter, cfg, ip_hash)
            raise AuthError("Empty Bearer token", status_code=401)
        if cfg.collab_admin_token and constant_time_equal(bearer, cfg.collab_admin_token):
            return AuthContext(
                tier="ADMIN",
                actor_id="admin:token",
                handle="admin",
                client_ip_hash=ip_hash,
                agent_role="admin",
            )
        if pre_authenticated_agent is BEARER_LOOKUP_MISS:
            _record_auth_failure(rate_limiter, cfg, ip_hash)
            raise AuthError("Invalid or unknown Bearer token", status_code=401)
        if pre_authenticated_agent is not None:
            handle = str(pre_authenticated_agent["handle"])
            role = _normalize_agent_role(pre_authenticated_agent.get("role"))
            return AuthContext(
                tier="AGENT_KEY",
                actor_id=f"agent:{handle}",
                handle=handle,
                client_ip_hash=ip_hash,
                agent_role=role,
            )
        agent = store.get_agent_by_key(bearer)
        if agent is not None:
            handle = str(agent["handle"])
            role = _normalize_agent_role(agent.get("role"))
            store.touch_agent_last_seen(
                handle,
                transport=transport,
                action=action,
                client_name=client_name,
            )
            return AuthContext(
                tier="AGENT_KEY",
                actor_id=f"agent:{handle}",
                handle=handle,
                client_ip_hash=ip_hash,
                agent_role=role,
            )
        _record_auth_failure(rate_limiter, cfg, ip_hash)
        raise AuthError("Invalid or unknown Bearer token", status_code=401)

    # 3. Zero-header MCP tool argument agent_key
    if agent_key_arg is not None and agent_key_arg.strip():
        raw_ak = agent_key_arg.strip()
        agent = store.get_agent_by_key(raw_ak)
        if agent is not None:
            handle = str(agent["handle"])
            role_arg = _normalize_agent_role(agent.get("role"))
            store.touch_agent_last_seen(
                handle,
                transport=transport,
                action=action,
                client_name=client_name,
            )
            return AuthContext(
                tier="AGENT_KEY",
                actor_id=f"agent:{handle}",
                handle=handle,
                client_ip_hash=ip_hash,
                agent_role=role_arg,
            )
        _record_auth_failure(rate_limiter, cfg, ip_hash)
        raise AuthError("Invalid or unknown agent_key", status_code=401)

    # 4. Caddy Google OAuth2 Forward-Auth header — ONLY honored on /admin* when enabled AND admin_emails configured!
    if cfg.trust_admin_sso_header and request_path.startswith("/admin"):
        sso_email = lower.get("x-auth-request-email", "").strip().lower()
        if sso_email and bool(cfg.admin_emails) and sso_email in cfg.admin_emails:
            return AuthContext(
                tier="ADMIN",
                actor_id=f"admin:{sso_email}",
                handle=sso_email.split("@")[0],
                client_ip_hash=ip_hash,
                agent_role="admin",
            )

    # 5. Default public ANONYMOUS tier
    return AuthContext(
        tier="ANONYMOUS",
        actor_id=f"anon:{ip_hash}",
        handle=f"anon-{ip_hash[:8]}",
        client_ip_hash=ip_hash,
        agent_role="contributor",
    )


class SlidingWindowRateLimiter:
    """Thread-safe in-process sliding-window rate limiter keyed by bucket string."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}
        self._windows: dict[str, float] = {}
        self._last_gc: float = 0.0

    def _evict_expired_buckets(self, ts: float) -> None:
        stale = [
            key
            for key, bq in self._buckets.items()
            if not bq or bq[-1] <= ts - self._windows.get(key, 60.0)
        ]
        for key in stale:
            self._buckets.pop(key, None)
            self._windows.pop(key, None)
        self._last_gc = ts

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
            if (len(self._buckets) > 512 and ts - self._last_gc >= 60.0) or len(self._buckets) > 4096:
                self._evict_expired_buckets(ts)
            dq = self._buckets.get(bucket_key)
            if dq is None:
                dq = deque()
                self._buckets[bucket_key] = dq
            self._windows[bucket_key] = window_seconds
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry = max(1, int(window_seconds - (ts - dq[0])) + 1)
                raise RateLimitError(
                    f"Rate limit exceeded ({limit} requests per {int(window_seconds)}s)",
                    retry_after_seconds=retry,
                )
            dq.append(ts)
