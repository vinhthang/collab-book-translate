"""Relational persistence store for collab-mcp (PostgreSQL collab_service schema + SQLite adapter)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Optional
import uuid

from scripts.mcp_server.auth import generate_default_handle, mint_agent_key, sha256_hex
from scripts.mcp_server.config import McpServerConfig

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,38}[a-z0-9]$")

SCHEMA_STATEMENTS_COMMON = (
    """
    CREATE TABLE IF NOT EXISTS agents (
        handle TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        model_info TEXT NOT NULL,
        key_sha256 TEXT NOT NULL UNIQUE,
        key_hint TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS books (
        slug TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        source_lang TEXT NOT NULL,
        target_lang TEXT NOT NULL,
        genre TEXT NOT NULL,
        license_id TEXT NOT NULL,
        license_url TEXT NOT NULL,
        status TEXT NOT NULL,
        glossary_sha TEXT NOT NULL,
        book_record_json TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        source_text TEXT NOT NULL DEFAULT '',
        source_filename TEXT NOT NULL DEFAULT 'source.md',
        proposed_by TEXT NOT NULL,
        release_tag TEXT NOT NULL DEFAULT '',
        release_url TEXT NOT NULL DEFAULT '',
        access_mode TEXT NOT NULL DEFAULT 'public',
        allowed_agents_json TEXT NOT NULL DEFAULT '[]',
        approval_mode TEXT NOT NULL DEFAULT 'auto',
        proposed_chapters_json TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chapters (
        book_slug TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        title TEXT NOT NULL,
        source_xml TEXT NOT NULL,
        status TEXT NOT NULL,
        claimed_by TEXT NOT NULL DEFAULT '',
        claim_token_sha256 TEXT NOT NULL DEFAULT '',
        claim_expires_at REAL NOT NULL DEFAULT 0,
        accepted_submission_id TEXT NOT NULL DEFAULT '',
        final_txt TEXT NOT NULL DEFAULT '',
        translation_xml TEXT NOT NULL DEFAULT '',
        opened_at REAL NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (book_slug, chapter_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS submissions (
        id TEXT PRIMARY KEY,
        book_slug TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        author_actor_id TEXT NOT NULL,
        author_handle TEXT NOT NULL,
        author_ip_hash TEXT NOT NULL DEFAULT '',
        engine_model TEXT NOT NULL,
        status TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        max_rounds INTEGER NOT NULL,
        workstream_id TEXT NOT NULL DEFAULT '',
        revision_token_sha256 TEXT NOT NULL,
        translation_xml TEXT NOT NULL,
        final_txt TEXT NOT NULL,
        additions_json TEXT NOT NULL,
        open_findings_json TEXT NOT NULL,
        security_flags_json TEXT NOT NULL,
        judge_summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS peer_reviews (
        id TEXT PRIMARY KEY,
        submission_id TEXT NOT NULL,
        reviewer_actor_id TEXT NOT NULL,
        reviewer_handle TEXT NOT NULL,
        scores_json TEXT NOT NULL,
        weighted_score REAL NOT NULL,
        issues_json TEXT NOT NULL,
        body_md TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)


def _utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CollabStore:
    """Unified persistence layer supporting PostgreSQL (collab_service schema) and SQLite."""

    def __init__(self, cfg: McpServerConfig) -> None:
        self._cfg = cfg
        self._use_pg = bool(cfg.database_url)
        self._lock = threading.RLock()
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._pg_mod: Any = None
        if self._use_pg:
            import psycopg
            from psycopg.rows import dict_row

            self._pg_mod = psycopg
            self._pg_dict_row = dict_row

    def _get_sqlite(self) -> sqlite3.Connection:
        if self._sqlite_conn is None:
            self._cfg.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._cfg.sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._sqlite_conn = conn
        return self._sqlite_conn

    def _exec(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any:
        with self._lock:
            if self._use_pg:
                pg_sql = sql.replace("?", "%s")
                with self._pg_mod.connect(
                    self._cfg.database_url,
                    row_factory=self._pg_dict_row,
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET search_path TO collab_service, public")
                        cur.execute(pg_sql, params)
                        if fetchone:
                            row = cur.fetchone()
                            conn.commit()
                            return dict(row) if row else None
                        if fetchall:
                            rows = cur.fetchall()
                            conn.commit()
                            return [dict(r) for r in rows]
                    conn.commit()
                    return None
            else:
                conn = self._get_sqlite()
                cur = conn.execute(sql, params)
                if fetchone:
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row) if row else None
                if fetchall:
                    rows = cur.fetchall()
                    conn.commit()
                    return [dict(r) for r in rows]
                conn.commit()
                return None

    def init_db(self) -> None:
        with self._lock:
            if self._use_pg:
                with self._pg_mod.connect(self._cfg.database_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("CREATE SCHEMA IF NOT EXISTS collab_service")
                        cur.execute("SET search_path TO collab_service, public")
                        for stmt in SCHEMA_STATEMENTS_COMMON:
                            cur.execute(stmt.replace("REAL", "DOUBLE PRECISION"))
                        cur.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS key_hint TEXT NOT NULL DEFAULT ''")
                        cur.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_seen_at TEXT NOT NULL DEFAULT ''")
                        cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'public'")
                        cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS allowed_agents_json TEXT NOT NULL DEFAULT '[]'")
                        cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS approval_mode TEXT NOT NULL DEFAULT 'auto'")
                        cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS proposed_chapters_json TEXT NOT NULL DEFAULT '[]'")
                        cur.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS author_ip_hash TEXT NOT NULL DEFAULT ''")
                    conn.commit()
            else:
                conn = self._get_sqlite()
                for stmt in SCHEMA_STATEMENTS_COMMON:
                    conn.execute(stmt)
                agent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
                if "key_hint" not in agent_cols:
                    conn.execute("ALTER TABLE agents ADD COLUMN key_hint TEXT NOT NULL DEFAULT ''")
                if "last_seen_at" not in agent_cols:
                    conn.execute("ALTER TABLE agents ADD COLUMN last_seen_at TEXT NOT NULL DEFAULT ''")
                book_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
                if "access_mode" not in book_cols:
                    conn.execute("ALTER TABLE books ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'public'")
                if "allowed_agents_json" not in book_cols:
                    conn.execute("ALTER TABLE books ADD COLUMN allowed_agents_json TEXT NOT NULL DEFAULT '[]'")
                if "approval_mode" not in book_cols:
                    conn.execute("ALTER TABLE books ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'auto'")
                if "proposed_chapters_json" not in book_cols:
                    conn.execute("ALTER TABLE books ADD COLUMN proposed_chapters_json TEXT NOT NULL DEFAULT '[]'")
                sub_cols = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)").fetchall()}
                if "author_ip_hash" not in sub_cols:
                    conn.execute("ALTER TABLE submissions ADD COLUMN author_ip_hash TEXT NOT NULL DEFAULT ''")
                conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._sqlite_conn is not None:
                self._sqlite_conn.close()
                self._sqlite_conn = None

    def register_agent(
        self,
        *,
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
    ) -> dict[str, str]:
        with self._lock:
            clean = handle.strip().lower()
            if clean:
                if not HANDLE_RE.match(clean):
                    raise ValueError(
                        "Agent handle must be 3-40 chars matching ^[a-z0-9][a-z0-9_-]{1,38}[a-z0-9]$"
                    )
                existing = self._exec(
                    "SELECT handle FROM agents WHERE handle = ?",
                    (clean,),
                    fetchone=True,
                )
                if existing is not None:
                    raise ValueError(f"Agent handle '{clean}' is already registered")
            else:
                for _ in range(16):
                    candidate = generate_default_handle(model_info=model_info, display_name=display_name)
                    if (
                        HANDLE_RE.match(candidate)
                        and self._exec("SELECT handle FROM agents WHERE handle = ?", (candidate,), fetchone=True)
                        is None
                    ):
                        clean = candidate
                        break
                if not clean:
                    raise RuntimeError("Failed to allocate unique default agent handle")

            raw_key, key_sha, key_hint = "", "", ""
            for _ in range(16):
                rk, ks, kh = mint_agent_key()
                if (
                    self._exec("SELECT handle FROM agents WHERE key_sha256 = ?", (ks,), fetchone=True)
                    is None
                ):
                    raw_key, key_sha, key_hint = rk, ks, kh
                    break
            if not raw_key:
                raise RuntimeError("Failed to mint unique 3-word AGENT_KEY")

            now = _utc_iso()
            disp = (display_name or clean).strip()
            mod = model_info.strip()
            self._exec(
                """
                INSERT INTO agents (handle, display_name, model_info, key_sha256, key_hint, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (clean, disp, mod, key_sha, key_hint, now, ""),
            )
            return {
                "handle": clean,
                "display_name": disp,
                "model_info": mod,
                "agent_key": raw_key,
                "key_hint": key_hint,
                "created_at": now,
            }

    def get_agent_by_key(self, raw_key: str) -> Optional[dict[str, Any]]:
        digest = sha256_hex(raw_key.strip())
        return self._exec(
            "SELECT handle, display_name, model_info, key_hint, created_at, last_seen_at FROM agents WHERE key_sha256 = ?",
            (digest,),
            fetchone=True,
        )

    def touch_agent_last_seen(self, handle: str) -> None:
        self._exec(
            "UPDATE agents SET last_seen_at = ? WHERE handle = ?",
            (_utc_iso(), handle),
        )

    def list_agents(self) -> list[dict[str, Any]]:
        return self._exec(
            "SELECT handle, display_name, model_info, key_hint, created_at, last_seen_at FROM agents ORDER BY created_at DESC",
            fetchall=True,
        )

    def resolve_agent_identifier(self, identifier: str) -> Optional[str]:
        """Resolve either a canonical handle or a raw AGENT_KEY (via SHA-256) to canonical handle."""
        token = identifier.strip()
        if not token:
            return None
        by_key = self.get_agent_by_key(token)
        if by_key is not None:
            return str(by_key["handle"])
        by_handle = self._exec(
            "SELECT handle FROM agents WHERE handle = ?",
            (token.lower(),),
            fetchone=True,
        )
        if by_handle is not None:
            return str(by_handle["handle"])
        return None

    def set_book_access(
        self,
        *,
        slug: str,
        access_mode: str,
        allowed_agents: list[str],
    ) -> dict[str, Any]:
        """Persist book access_mode and canonical allowed_agents allowlist."""
        self._exec(
            """
            UPDATE books
            SET access_mode = ?, allowed_agents_json = ?, updated_at = ?
            WHERE slug = ?
            """,
            (access_mode, json.dumps(allowed_agents, ensure_ascii=False), _utc_iso(), slug),
        )
        book = self.get_book(slug)
        if book is None:
            raise KeyError(f"Book '{slug}' not found")
        return book

    def set_book_approval_mode(
        self,
        *,
        slug: str,
        approval_mode: str,
    ) -> dict[str, Any]:
        """Persist per-book chapter approval policy ('auto' or 'manual')."""
        self._exec(
            "UPDATE books SET approval_mode = ?, updated_at = ? WHERE slug = ?",
            (approval_mode, _utc_iso(), slug),
        )
        book = self.get_book(slug)
        if book is None:
            raise KeyError(f"Book '{slug}' not found")
        return book

    def set_book_status(
        self,
        *,
        slug: str,
        status: str,
    ) -> dict[str, Any]:
        """Persist book lifecycle status directly."""
        self._exec(
            "UPDATE books SET status = ?, updated_at = ? WHERE slug = ?",
            (status, _utc_iso(), slug),
        )
        book = self.get_book(slug)
        if book is None:
            raise KeyError(f"Book '{slug}' not found")
        return book

    def upsert_book(
        self,
        *,
        slug: str,
        title: str,
        author: str,
        source_lang: str,
        target_lang: str,
        genre: str,
        license_id: str,
        license_url: str,
        status: str,
        glossary_sha: str,
        book_record: dict[str, Any],
        manifest: dict[str, Any],
        proposed_by: str,
        source_text: str = "",
        source_filename: str = "source.md",
        release_tag: str = "",
        release_url: str = "",
        proposed_chapters: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        now = _utc_iso()
        ch_json = json.dumps(proposed_chapters or [], ensure_ascii=False)
        self._exec(
            """
            INSERT INTO books (
                slug, title, author, source_lang, target_lang, genre,
                license_id, license_url, status, glossary_sha, book_record_json,
                manifest_json, source_text, source_filename, proposed_by,
                release_tag, release_url, proposed_chapters_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                source_lang = excluded.source_lang,
                target_lang = excluded.target_lang,
                genre = excluded.genre,
                license_id = excluded.license_id,
                license_url = excluded.license_url,
                status = excluded.status,
                glossary_sha = excluded.glossary_sha,
                book_record_json = excluded.book_record_json,
                manifest_json = excluded.manifest_json,
                source_text = CASE WHEN excluded.source_text != '' THEN excluded.source_text ELSE books.source_text END,
                source_filename = excluded.source_filename,
                release_tag = CASE WHEN excluded.release_tag != '' THEN excluded.release_tag ELSE books.release_tag END,
                release_url = CASE WHEN excluded.release_url != '' THEN excluded.release_url ELSE books.release_url END,
                proposed_chapters_json = CASE WHEN excluded.proposed_chapters_json != '[]' THEN excluded.proposed_chapters_json ELSE books.proposed_chapters_json END,
                updated_at = excluded.updated_at
            """,
            (
                slug,
                title,
                author,
                source_lang,
                target_lang,
                genre,
                license_id,
                license_url,
                status,
                glossary_sha,
                json.dumps(book_record, ensure_ascii=False),
                json.dumps(manifest, ensure_ascii=False),
                source_text,
                source_filename,
                proposed_by,
                release_tag,
                release_url,
                ch_json,
                now,
            ),
        )
        book = self.get_book(slug)
        if book is None:
            raise RuntimeError(f"Failed to persist book '{slug}'")
        return book

    def _hydrate_book(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["book_record"] = json.loads(out.pop("book_record_json") or "{}")
        out["manifest"] = json.loads(out.pop("manifest_json") or "{}")
        out["access_mode"] = str(out.get("access_mode") or "public")
        out["allowed_agents"] = json.loads(out.pop("allowed_agents_json", "[]") or "[]")
        out["approval_mode"] = str(out.get("approval_mode") or "auto")
        out["proposed_chapters"] = json.loads(out.pop("proposed_chapters_json", "[]") or "[]")
        return out

    def get_book(self, slug: str) -> Optional[dict[str, Any]]:
        row = self._exec("SELECT * FROM books WHERE slug = ?", (slug,), fetchone=True)
        return self._hydrate_book(row) if row else None

    def list_books(self, include_pending: bool = False) -> list[dict[str, Any]]:
        if include_pending:
            rows = self._exec("SELECT * FROM books ORDER BY slug ASC", fetchall=True)
        else:
            rows = self._exec(
                "SELECT * FROM books WHERE status != 'rejected' ORDER BY slug ASC",
                fetchall=True,
            )
        return [self._hydrate_book(r) for r in rows]

    def upsert_chapter(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        chapter_num: int,
        title: str,
        source_xml: str,
        status: str,
        final_txt: str = "",
        translation_xml: str = "",
        opened_at: Optional[float] = None,
    ) -> None:
        now_iso = _utc_iso()
        opened = time.time() if opened_at is None else opened_at
        self._exec(
            """
            INSERT INTO chapters (
                book_slug, chapter_id, chapter_num, title, source_xml, status,
                final_txt, translation_xml, opened_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_slug, chapter_id) DO UPDATE SET
                chapter_num = excluded.chapter_num,
                title = excluded.title,
                source_xml = excluded.source_xml,
                status = excluded.status,
                final_txt = CASE WHEN excluded.final_txt != '' THEN excluded.final_txt ELSE chapters.final_txt END,
                translation_xml = CASE WHEN excluded.translation_xml != '' THEN excluded.translation_xml ELSE chapters.translation_xml END,
                updated_at = excluded.updated_at
            """,
            (
                book_slug,
                chapter_id,
                chapter_num,
                title,
                source_xml,
                status,
                final_txt,
                translation_xml,
                opened,
                now_iso,
            ),
        )

    def get_chapter(self, book_slug: str, chapter_id: str) -> Optional[dict[str, Any]]:
        return self._exec(
            "SELECT * FROM chapters WHERE book_slug = ? AND chapter_id = ?",
            (book_slug, chapter_id),
            fetchone=True,
        )

    def list_chapters(self, book_slug: str) -> list[dict[str, Any]]:
        return self._exec(
            "SELECT * FROM chapters WHERE book_slug = ? ORDER BY chapter_num ASC",
            (book_slug,),
            fetchall=True,
        )

    def claim_chapter(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        actor_id: str,
        claim_token_sha256: str,
        expires_at: float,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else now
        with self._lock:
            ch = self.get_chapter(book_slug, chapter_id)
            if ch is None:
                raise KeyError(f"Chapter {book_slug}/{chapter_id} not found")
            if ch["status"] != "open":
                raise ValueError(f"Chapter {book_slug}/{chapter_id} is '{ch['status']}', not 'open'")
            if ch["claimed_by"] and ch["claimed_by"] != actor_id and float(ch["claim_expires_at"]) > ts:
                raise PermissionError(
                    f"Chapter {book_slug}/{chapter_id} is currently claimed until {int(ch['claim_expires_at'])}"
                )
            self._exec(
                """
                UPDATE chapters
                SET claimed_by = ?, claim_token_sha256 = ?, claim_expires_at = ?, updated_at = ?
                WHERE book_slug = ? AND chapter_id = ?
                """,
                (actor_id, claim_token_sha256, expires_at, _utc_iso(), book_slug, chapter_id),
            )
            updated = self.get_chapter(book_slug, chapter_id)
            if updated is None:
                raise RuntimeError(f"Failed to update claim for {book_slug}/{chapter_id}")
            return updated

    def create_submission(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        author_actor_id: str,
        author_handle: str,
        author_ip_hash: str = "",
        engine_model: str,
        status: str,
        round_number: int,
        max_rounds: int,
        workstream_id: str,
        revision_token_sha256: str,
        translation_xml: str,
        final_txt: str,
        additions: dict[str, Any],
        open_findings: list[dict[str, Any]],
        security_flags: list[str],
        judge_summary: str = "",
    ) -> dict[str, Any]:
        sub_id = str(uuid.uuid4())
        now = _utc_iso()
        self._exec(
            """
            INSERT INTO submissions (
                id, book_slug, chapter_id, author_actor_id, author_handle, author_ip_hash,
                engine_model, status, round_number, max_rounds, workstream_id,
                revision_token_sha256, translation_xml, final_txt, additions_json,
                open_findings_json, security_flags_json, judge_summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sub_id,
                book_slug,
                chapter_id,
                author_actor_id,
                author_handle,
                author_ip_hash,
                engine_model,
                status,
                round_number,
                max_rounds,
                workstream_id,
                revision_token_sha256,
                translation_xml,
                final_txt,
                json.dumps(additions, ensure_ascii=False),
                json.dumps(open_findings, ensure_ascii=False),
                json.dumps(security_flags, ensure_ascii=False),
                judge_summary,
                now,
                now,
            ),
        )
        sub = self.get_submission(sub_id)
        if sub is None:
            raise RuntimeError(f"Failed to persist submission '{sub_id}'")
        return sub

    def update_submission(
        self,
        submission_id: str,
        *,
        status: Optional[str] = None,
        round_number: Optional[int] = None,
        workstream_id: Optional[str] = None,
        translation_xml: Optional[str] = None,
        final_txt: Optional[str] = None,
        additions: Optional[dict[str, Any]] = None,
        open_findings: Optional[list[dict[str, Any]]] = None,
        security_flags: Optional[list[str]] = None,
        judge_summary: Optional[str] = None,
    ) -> dict[str, Any]:
        current = self.get_submission(submission_id)
        if current is None:
            raise KeyError(f"Submission {submission_id} not found")
        now = _utc_iso()
        self._exec(
            """
            UPDATE submissions SET
                status = ?,
                round_number = ?,
                workstream_id = ?,
                translation_xml = ?,
                final_txt = ?,
                additions_json = ?,
                open_findings_json = ?,
                security_flags_json = ?,
                judge_summary = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status if status is not None else current["status"],
                round_number if round_number is not None else current["round_number"],
                workstream_id if workstream_id is not None else current["workstream_id"],
                translation_xml if translation_xml is not None else current["translation_xml"],
                final_txt if final_txt is not None else current["final_txt"],
                json.dumps(additions if additions is not None else current["additions"], ensure_ascii=False),
                json.dumps(open_findings if open_findings is not None else current["open_findings"], ensure_ascii=False),
                json.dumps(security_flags if security_flags is not None else current["security_flags"], ensure_ascii=False),
                judge_summary if judge_summary is not None else current["judge_summary"],
                now,
                submission_id,
            ),
        )
        updated = self.get_submission(submission_id)
        if updated is None:
            raise RuntimeError(f"Failed to update submission '{submission_id}'")
        return updated

    def _hydrate_submission(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["additions"] = json.loads(out.pop("additions_json") or '{"proposals":[]}')
        out["open_findings"] = json.loads(out.pop("open_findings_json") or "[]")
        out["security_flags"] = json.loads(out.pop("security_flags_json") or "[]")
        return out

    def get_submission(self, submission_id: str) -> Optional[dict[str, Any]]:
        row = self._exec("SELECT * FROM submissions WHERE id = ?", (submission_id,), fetchone=True)
        return self._hydrate_submission(row) if row else None

    def list_submissions(
        self,
        *,
        book_slug: Optional[str] = None,
        chapter_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if book_slug:
            clauses.append("book_slug = ?")
            params.append(book_slug)
        if chapter_id:
            clauses.append("chapter_id = ?")
            params.append(chapter_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._exec(
            f"SELECT * FROM submissions {where} ORDER BY created_at DESC",
            tuple(params),
            fetchall=True,
        )
        return [self._hydrate_submission(r) for r in rows]

    def mark_chapter_accepted(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        submission_id: str,
        translation_xml: str,
        final_txt: str,
    ) -> None:
        self._exec(
            """
            UPDATE chapters SET
                status = 'accepted',
                accepted_submission_id = ?,
                translation_xml = ?,
                final_txt = ?,
                claimed_by = '',
                claim_token_sha256 = '',
                claim_expires_at = 0,
                updated_at = ?
            WHERE book_slug = ? AND chapter_id = ?
            """,
            (submission_id, translation_xml, final_txt, _utc_iso(), book_slug, chapter_id),
        )

    def add_peer_review(
        self,
        *,
        submission_id: str,
        reviewer_actor_id: str,
        reviewer_handle: str,
        scores: dict[str, float],
        weighted_score: float,
        issues: list[dict[str, Any]],
        body_md: str,
    ) -> dict[str, Any]:
        rev_id = str(uuid.uuid4())
        now = _utc_iso()
        self._exec(
            """
            INSERT INTO peer_reviews (
                id, submission_id, reviewer_actor_id, reviewer_handle,
                scores_json, weighted_score, issues_json, body_md, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rev_id,
                submission_id,
                reviewer_actor_id,
                reviewer_handle,
                json.dumps(scores, ensure_ascii=False),
                weighted_score,
                json.dumps(issues, ensure_ascii=False),
                body_md,
                now,
            ),
        )
        return {
            "id": rev_id,
            "submission_id": submission_id,
            "reviewer_actor_id": reviewer_actor_id,
            "reviewer_handle": reviewer_handle,
            "scores": scores,
            "weighted_score": weighted_score,
            "issues": issues,
            "body_md": body_md,
            "created_at": now,
        }

    def list_peer_reviews(self, submission_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM peer_reviews WHERE submission_id = ? ORDER BY created_at ASC",
            (submission_id,),
            fetchall=True,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["scores"] = json.loads(item.pop("scores_json") or "{}")
            item["issues"] = json.loads(item.pop("issues_json") or "[]")
            out.append(item)
        return out

    def bootstrap_from_books_dir(self, books_repo_dir: Path) -> int:
        """Idempotently import existing books/ and runs/ from collab-books checkout."""
        books_dir = books_repo_dir / "books"
        runs_dir = books_repo_dir / "runs"
        if not books_dir.is_dir():
            return 0

        imported = 0
        for manifest_path in sorted(books_dir.glob("*/manifest.json")):
            slug = manifest_path.parent.name
            if self.get_book(slug) is not None:
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_dir = runs_dir / slug
            book_record = {}
            if (run_dir / "book_record.json").is_file():
                book_record = json.loads((run_dir / "book_record.json").read_text(encoding="utf-8"))
            checklist = {}
            if (run_dir / "checklist.json").is_file():
                raw_cl = json.loads((run_dir / "checklist.json").read_text(encoding="utf-8"))
                if isinstance(raw_cl, dict):
                    checklist = raw_cl
            glossary_sha = str(checklist.get("glossary_sha") or "")

            ch_dirs = sorted(p for p in run_dir.glob("ch[0-9]*") if p.is_dir()) if run_dir.is_dir() else []
            all_done = bool(ch_dirs) and all((d / "final.txt").is_file() for d in ch_dirs)
            book_status = "published" if all_done else ("active" if ch_dirs else "pending_seed")
            rel_tag = f"{slug}-v1.0" if all_done else ""
            rel_url = (
                f"https://github.com/{self._cfg.books_repo_full_name}/releases/tag/{rel_tag}"
                if rel_tag
                else ""
            )

            self.upsert_book(
                slug=slug,
                title=str(manifest.get("title") or slug),
                author=str(manifest.get("author") or "Unknown"),
                source_lang=str(manifest.get("source_lang") or "en"),
                target_lang=str(manifest.get("target_lang") or "vi"),
                genre=str(manifest.get("genre") or "fiction"),
                license_id=str(manifest.get("license") or "public-domain"),
                license_url=str(manifest.get("license_url") or ""),
                status=book_status,
                glossary_sha=glossary_sha,
                book_record=book_record,
                manifest=manifest,
                proposed_by="bootstrap",
                release_tag=rel_tag,
                release_url=rel_url,
            )

            for idx, ch_dir in enumerate(ch_dirs, start=1):
                ch_id = ch_dir.name
                src_xml = (ch_dir / "source.txt").read_text(encoding="utf-8") if (ch_dir / "source.txt").is_file() else ""
                tr_xml = (ch_dir / "translation.xml").read_text(encoding="utf-8") if (ch_dir / "translation.xml").is_file() else ""
                fin_txt = (ch_dir / "final.txt").read_text(encoding="utf-8") if (ch_dir / "final.txt").is_file() else ""
                ch_status = "accepted" if fin_txt.strip() else "open"
                self.upsert_chapter(
                    book_slug=slug,
                    chapter_id=ch_id,
                    chapter_num=idx,
                    title=f"Chapter {idx}",
                    source_xml=src_xml,
                    status=ch_status,
                    final_txt=fin_txt,
                    translation_xml=tr_xml,
                )
            imported += 1
        return imported
