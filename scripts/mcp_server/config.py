"""Configuration loader for the standalone collab-mcp service."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class McpServerConfig:
    database_url: str
    sqlite_path: Path
    books_repo_dir: Path
    books_repo_full_name: str
    review_server_url: str
    review_bearer_token: str
    collab_admin_token: str
    admin_emails: frozenset[str]
    trust_admin_sso_header: bool
    github_token: str
    bot_git_name: str
    bot_git_email: str
    anon_read_rpm: int = 120
    anon_write_rpm: int = 15
    agent_write_rpm: int = 60
    claim_ttl_seconds: int = 7200
    fallback_hours: float = 48.0
    max_revise_rounds: int = 3
    host: str = "0.0.0.0"
    port: int = 8766
    public_base_url: str = "https://collab.vinhthang.dev"
    review_engine: str = "gemini"


def load_mcp_server_config() -> McpServerConfig:
    """Load McpServerConfig from environment variables."""
    default_data_dir = Path(os.environ.get("COLLAB_DATA_DIR", tempfile.gettempdir())) / "collab-mcp"
    raw_emails = os.environ.get("COLLAB_ADMIN_EMAILS", "")
    admin_emails = frozenset(
        item.strip().lower() for item in raw_emails.split(",") if item.strip()
    )
    trust_sso = os.environ.get("TRUST_ADMIN_SSO_HEADER", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return McpServerConfig(
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        sqlite_path=Path(os.environ.get("COLLAB_SQLITE_PATH", str(default_data_dir / "collab.sqlite3"))),
        books_repo_dir=Path(os.environ.get("COLLAB_BOOKS_DIR", str(default_data_dir / "collab-books"))),
        books_repo_full_name=os.environ.get("COLLAB_BOOKS_REPO", "vinhthang/collab-books").strip(),
        review_server_url=os.environ.get("REVIEW_SERVER_URL", "https://review.vinhthang.dev").strip().rstrip("/"),
        review_bearer_token=os.environ.get("REVIEW_BEARER_TOKEN", "").strip(),
        collab_admin_token=os.environ.get("COLLAB_ADMIN_TOKEN", "").strip(),
        admin_emails=admin_emails,
        trust_admin_sso_header=trust_sso,
        github_token=os.environ.get("GITHUB_TOKEN", "").strip(),
        bot_git_name=os.environ.get("BOT_GIT_NAME", "collab-mcp-bot").strip(),
        bot_git_email=os.environ.get("BOT_GIT_EMAIL", "collab-mcp-bot@users.noreply.github.com").strip(),
        anon_read_rpm=max(1, int(os.environ.get("ANON_READ_RPM", "120"))),
        anon_write_rpm=max(1, int(os.environ.get("ANON_WRITE_RPM", "15"))),
        agent_write_rpm=max(1, int(os.environ.get("AGENT_WRITE_RPM", "60"))),
        claim_ttl_seconds=max(60, int(os.environ.get("CLAIM_TTL_SECONDS", "7200"))),
        fallback_hours=max(0.1, float(os.environ.get("FALLBACK_HOURS", "48.0"))),
        max_revise_rounds=max(1, int(os.environ.get("MAX_REVISE_ROUNDS", "3"))),
        host=os.environ.get("HOST", "0.0.0.0").strip(),
        port=int(os.environ.get("PORT", "8766")),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "https://collab.vinhthang.dev").strip().rstrip("/"),
        review_engine=os.environ.get("COLLAB_REVIEW_ENGINE", "gemini").strip(),
    )
