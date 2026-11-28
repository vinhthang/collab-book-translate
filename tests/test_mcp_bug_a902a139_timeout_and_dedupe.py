"""TDD tests for bug a902a139: sync evaluating lock, duplicate supersede, operational timeout handling, expired claim visibility, and admin rerun."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from scripts.mcp_server.app import create_app
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.review_client import ReviewServerClient, ReviewServerError
from scripts.mcp_server.service import AuthContext, CollabMcpService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _setup_env(tmp_path: Path, review_client: Any):
    bare_remote = tmp_path / "remote" / "collab-books.git"
    bare_remote.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)

    work_repo = tmp_path / "work" / "collab-books"
    work_repo.mkdir(parents=True)
    _git(work_repo, "init", "-b", "main")
    _git(work_repo, "remote", "add", "origin", str(bare_remote))
    (work_repo / "README.md").write_text("# Collab Books\n", encoding="utf-8")
    _git(work_repo, "add", "README.md")
    _git(work_repo, "-c", "user.name=Bot", "-c", "user.email=b@e.com", "commit", "-m", "init")
    _git(work_repo, "push", "-u", "origin", "main")

    cfg = McpServerConfig(
        database_url="",
        sqlite_path=tmp_path / "collab_mcp.db",
        books_repo_dir=work_repo,
        books_repo_full_name="vinhthang/collab-books",
        collab_admin_token="admin-secret-token",
        review_server_url="https://review.vinhthang.dev",
        review_bearer_token="rev-token",
        admin_emails=frozenset({"admin@vinhthang.dev"}),
        trust_admin_sso_header=True,
        github_token="",
        public_base_url="https://collab.vinhthang.dev",
        bot_git_name="Collab Bot",
        bot_git_email="bot@vinhthang.dev",
        anon_read_rpm=500,
        anon_write_rpm=200,
        agent_write_rpm=200,
    )
    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(cfg=cfg, store=store, review_client=review_client, git_sync=sync)

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: review_client):
        app = create_app(cfg=cfg, service=svc)
    return cfg, store, svc, app, sso_hdr


def _propose_active_book(
    svc: CollabMcpService, store: CollabStore, slug: str = "gift-test", *, suffix: str = ""
) -> tuple[AuthContext, AuthContext, str, str]:
    aa_rec = store.register_agent(handle=f"aa-test{suffix}", model_info="opus", role="agent_admin")
    tr_rec = store.register_agent(handle=f"muse-test{suffix}", model_info="muse", role="contributor")
    aa_ctx = AuthContext(tier="AGENT_KEY", actor_id=f"agent:{aa_rec['handle']}", handle=aa_rec["handle"], client_ip_hash="h1", agent_role="agent_admin")
    tr_ctx = AuthContext(tier="AGENT_KEY", actor_id=f"agent:{tr_rec['handle']}", handle=tr_rec["handle"], client_ip_hash="h2", agent_role="contributor")

    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nOne dollar.\n</original>\n</segment>\n'
    tr_xml = '<segment id="S-0001" type="paragraph">\n<original>\nOne dollar.\n</original>\n<translate>\nMột đô la.\n</translate>\n</segment>\n'
    svc.submit_book_proposal(
        aa_ctx,
        slug=slug,
        title="Gift Test",
        author="O. Henry",
        source_lang="en",
        license_id="public-domain",
        license_url="https://example.org/pd",
        source_filename="gift.md",
        book_record={"terms": {"Jim": "Jim"}},
        chapters=[
            {"chapter_id": "ch01", "title": "Ch 1", "source_xml": src_xml, "translation_xml": tr_xml},
            {"chapter_id": "ch02", "title": "Ch 2", "source_xml": src_xml},
        ],
        unlock_all_chapters=True,
    )
    store.mark_chapter_accepted(
        book_slug=slug,
        chapter_id="ch01",
        submission_id="seed-1",
        translation_xml=tr_xml,
        final_txt="Một đô la.",
    )
    return aa_ctx, tr_ctx, src_xml, tr_xml


def test_sync_submit_marks_evaluating_before_review_blocking_concurrent_duplicate(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    should_raise = {"value": False}

    class _SlowReviewClient:
        def __init__(self) -> None:
            self.ws_seq = 0

        def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
            self.ws_seq += 1
            return {"id": f"ws-{self.ws_seq}", "currentStage": "TRANSLATION_REVIEW", "stageStatus": "INIT"}

        def run_translation_round(self, *, workstream_id: str, content: str, rebuttals: list[dict[str, str]] | None = None) -> dict[str, Any]:
            started.set()
            release.wait(timeout=5.0)
            if should_raise["value"]:
                raise ReviewServerError("upstream timeout", status_code=504)
            return {
                "verdict": "PASS",
                "stageStatus": "GOVERNANCE_CLEAN",
                "roundNumber": 1,
                "p0Count": 0,
                "p1Count": 0,
                "p2Count": 0,
                "judgeSummary": "Clean.",
                "openFindings": [],
            }

        def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
            return {"id": workstream_id, "stageStatus": "APPROVED"}

    rc = _SlowReviewClient()
    _, store, svc, _, _ = _setup_env(tmp_path, rc)
    try:
        _, tr_ctx, _, tr_xml = _propose_active_book(svc, store)
        results: list[tuple[str, Any]] = []

        def _worker() -> None:
            try:
                res = svc.submit_translation(tr_ctx, book_slug="gift-test", chapter_id="ch02", translation_xml=tr_xml)
                results.append(("ok", res))
            except Exception as exc:
                results.append(("err", exc))

        t = threading.Thread(target=_worker)
        t.start()
        assert started.wait(timeout=3.0)

        # While first synchronous round is in-flight, concurrent Round-1 submit must get 409 evaluating
        with pytest.raises(Exception) as exc_info:
            svc.submit_translation(tr_ctx, book_slug="gift-test", chapter_id="ch02", translation_xml=tr_xml)
        assert getattr(exc_info.value, "status_code", None) == 409
        assert exc_info.value.details["blocking_status"] == "evaluating"

        # Now make the in-flight round raise -> submission must revert cleanly to remediation_required with cleared workstream_id
        should_raise["value"] = True
        release.set()
        t.join(timeout=3.0)
        assert results and results[0][0] == "err"

        subs = store.list_submissions(book_slug="gift-test", chapter_id="ch02")
        assert len(subs) == 1
        assert subs[0]["status"] == "remediation_required"
        assert subs[0]["workstream_id"] == ""
    finally:
        store.close()


def test_fresh_round1_supersedes_same_author_stale_remediation_drafts(tmp_path: Path) -> None:
    class _PassClient:
        ws_seq = 0

        def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
            self.ws_seq += 1
            return {"id": f"ws-{self.ws_seq}", "currentStage": "TRANSLATION_REVIEW", "stageStatus": "INIT"}

        def run_translation_round(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "verdict": "PASS",
                "stageStatus": "GOVERNANCE_CLEAN",
                "roundNumber": 1,
                "p0Count": 0,
                "p1Count": 0,
                "p2Count": 0,
                "judgeSummary": "Clean.",
                "openFindings": [],
            }

        def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
            return {"id": workstream_id, "stageStatus": "APPROVED"}

    _, store, svc, _, _ = _setup_env(tmp_path, _PassClient())
    try:
        _, tr_ctx, _, tr_xml = _propose_active_book(svc, store)
        stale = store.create_submission(
            book_slug="gift-test",
            chapter_id="ch02",
            author_actor_id=tr_ctx.actor_id,
            author_handle=tr_ctx.handle,
            engine_model="muse",
            status="remediation_required",
            round_number=1,
            max_rounds=3,
            workstream_id="ws-old",
            revision_token_sha256="x" * 64,
            translation_xml=tr_xml,
            final_txt="Một đô la.",
            additions={"proposals": []},
            open_findings=[],
            security_flags=[],
            judge_summary="Old timeout",
        )
        code, payload = svc.submit_translation(tr_ctx, book_slug="gift-test", chapter_id="ch02", translation_xml=tr_xml)
        assert code == 200
        assert payload["submission"]["status"] == "pending_review"
        stale_after = store.get_submission(stale["id"])
        assert stale_after is not None and stale_after["status"] == "superseded"
    finally:
        store.close()


def test_operational_specialist_timeout_preserves_round_and_resets_workstream(tmp_path: Path) -> None:
    op_finding = {
        "findingKey": "CONTRACT::global::specialist-vi-terminology-pronoun-hawk-gemini-failed-agy-turn-ti",
        "severity": "P1",
        "symptom": "Specialist vi-terminology-pronoun-hawk (gemini) failed: agy turn timed out after 120000ms",
        "fixRecommendation": "Check LLM provider health/configuration and rerun the round",
        "status": "PERSISTENT",
    }
    tr_finding = {
        "findingKey": "VI::S-0001::wrong-pronoun",
        "severity": "P1",
        "symptom": "Pronoun mismatch in S-0001",
        "fixRecommendation": "Fix pronoun",
        "status": "OPEN",
    }

    class _TimeoutThenMixedClient:
        def __init__(self) -> None:
            self.created_ws: list[str] = []
            self.mode = "timeout"

        def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
            ws_id = f"ws-{len(self.created_ws) + 1}"
            self.created_ws.append(ws_id)
            return {"id": ws_id, "currentStage": "TRANSLATION_REVIEW", "stageStatus": "INIT"}

        def run_translation_round(self, *, workstream_id: str, content: str, rebuttals: list[dict[str, str]] | None = None) -> dict[str, Any]:
            if self.mode == "timeout":
                return {
                    "verdict": "BLOCK",
                    "stageStatus": "ESCALATED_TO_HUMAN",
                    "roundNumber": 3,
                    "p0Count": 0,
                    "p1Count": 1,
                    "p2Count": 0,
                    "judgeSummary": "Timed out.",
                    "openFindings": [op_finding],
                }
            if self.mode == "pass":
                return {
                    "verdict": "PASS",
                    "stageStatus": "GOVERNANCE_CLEAN",
                    "roundNumber": 1,
                    "p0Count": 0,
                    "p1Count": 0,
                    "p2Count": 0,
                    "judgeSummary": "Clean.",
                    "openFindings": [],
                }
            return {
                "verdict": "BLOCK",
                "stageStatus": "ESCALATED_TO_HUMAN",
                "roundNumber": 3,
                "p0Count": 0,
                "p1Count": 2,
                "p2Count": 0,
                "judgeSummary": "Real translation flaw + timeout.",
                "openFindings": [op_finding, tr_finding],
            }

        def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
            return {"id": workstream_id, "stageStatus": "APPROVED"}

    rc = _TimeoutThenMixedClient()
    _, store, svc, _, _ = _setup_env(tmp_path, rc)
    try:
        _, tr_ctx, _, tr_xml = _propose_active_book(svc, store)

        # Round 1 hits operational-only timeout even when upstream claims ESCALATED_TO_HUMAN round 3
        _, p1 = svc.submit_translation(tr_ctx, book_slug="gift-test", chapter_id="ch02", translation_xml=tr_xml)
        sub1 = p1["submission"]
        assert sub1["status"] == "remediation_required"
        assert sub1["round_number"] == 0
        assert sub1["workstream_id"] == ""
        assert "not counted" in sub1["judge_summary"].lower()

        # Retrying that submission_id opens a NEW workstream ("ws-2") and passes cleanly
        rc.mode = "pass"
        _, p2 = svc.submit_translation(
            tr_ctx,
            book_slug="gift-test",
            chapter_id="ch02",
            translation_xml=tr_xml,
            submission_id=sub1["id"],
        )
        assert rc.created_ws == ["ws-1", "ws-2"]
        assert p2["submission"]["status"] == "pending_review"
        assert p2["submission"]["round_number"] == 1

        # Mixed operational + real translation finding DOES escalate cleanly when at max rounds
        rc.mode = "mixed"
        _, tr2, _, _ = _propose_active_book(svc, store, slug="gift-mixed", suffix="-mixed")
        _, pm = svc.submit_translation(tr2, book_slug="gift-mixed", chapter_id="ch02", translation_xml=tr_xml)
        assert pm["submission"]["status"] == "escalated_to_admin"
    finally:
        store.close()


def test_admin_rerun_action_and_escalated_supersede_and_client_timeout(tmp_path: Path) -> None:
    class _RerunClient:
        def __init__(self) -> None:
            self.created: list[str] = []

        def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
            ws_id = f"ws-rerun-{len(self.created) + 1}"
            self.created.append(ws_id)
            return {"id": ws_id, "currentStage": "TRANSLATION_REVIEW", "stageStatus": "INIT"}

        def run_translation_round(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "verdict": "PASS",
                "stageStatus": "GOVERNANCE_CLEAN",
                "roundNumber": 1,
                "p0Count": 0,
                "p1Count": 0,
                "p2Count": 0,
                "judgeSummary": "Clean on rerun.",
                "openFindings": [],
            }

        def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
            return {"id": workstream_id, "stageStatus": "APPROVED"}

    rc = _RerunClient()
    cfg, store, svc, app, sso_hdr = _setup_env(tmp_path, rc)
    try:
        assert ReviewServerClient(cfg)._timeout >= 480.0
        aa_ctx, tr_ctx, _, tr_xml = _propose_active_book(svc, store)
        admin_ctx = AuthContext(tier="ADMIN", actor_id="admin:owner", handle="owner", client_ip_hash="h0")

        esc_sub = store.create_submission(
            book_slug="gift-test",
            chapter_id="ch02",
            author_actor_id=tr_ctx.actor_id,
            author_handle=tr_ctx.handle,
            engine_model="muse",
            status="escalated_to_admin",
            round_number=3,
            max_rounds=3,
            workstream_id="ws-exhausted",
            revision_token_sha256="y" * 64,
            translation_xml=tr_xml,
            final_txt="Một đô la.",
            additions={"proposals": []},
            open_findings=[],
            security_flags=[],
            judge_summary="Old escalation.",
        )
        other_esc = store.create_submission(
            book_slug="gift-test",
            chapter_id="ch02",
            author_actor_id=tr_ctx.actor_id,
            author_handle=tr_ctx.handle,
            engine_model="muse",
            status="escalated_to_admin",
            round_number=3,
            max_rounds=3,
            workstream_id="ws-other",
            revision_token_sha256="z" * 64,
            translation_xml=tr_xml,
            final_txt="Một đô la.",
            additions={"proposals": []},
            open_findings=[],
            security_flags=[],
            judge_summary="Other escalation.",
        )

        # /admin renders data-action="rerun" for escalated_to_admin submissions
        with TestClient(app) as client:
            adm_html = client.get("/admin", headers=sso_hdr).text
            assert 'data-action="rerun"' in adm_html

        # Non-ADMIN (agent_admin) gets 403 for action="rerun"
        with pytest.raises(Exception) as exc_info:
            svc.manage_flagged_submission(aa_ctx, submission_id=esc_sub["id"], action="rerun")
        assert getattr(exc_info.value, "status_code", None) == 403

        # ADMIN rerun opens fresh workstream, clears exhausted round_number, and holds in pending_review/pending_admin_approval
        updated = svc.manage_flagged_submission(admin_ctx, submission_id=esc_sub["id"], action="rerun")
        assert rc.created == ["ws-rerun-1"]
        assert updated["workstream_id"] == "ws-rerun-1"
        assert updated["status"] in {"pending_review", "pending_admin_approval"}

        # supersede_other_chapter_submissions also clears sibling escalated_to_admin drafts
        count = store.supersede_other_chapter_submissions(
            book_slug="gift-test",
            chapter_id="ch02",
            accepted_submission_id=esc_sub["id"],
        )
        assert count >= 1
        assert (store.get_submission(other_esc["id"]) or {}).get("status") == "superseded"
    finally:
        store.close()


def test_expired_claim_hidden_in_get_book_and_get_chapter(tmp_path: Path) -> None:
    class _NoopClient:
        pass

    _, store, svc, _, _ = _setup_env(tmp_path, _NoopClient())
    try:
        _, tr_ctx, _, _ = _propose_active_book(svc, store)
        svc.claim_chapter(tr_ctx, book_slug="gift-test", chapter_id="ch02")

        # While valid, get_book and get_chapter expose claimed_by
        bk_live = svc.get_book(tr_ctx, book_slug="gift-test")
        ch02_live = next(c for c in bk_live["chapters"] if c["chapter_id"] == "ch02")
        assert ch02_live["claimed_by"] == tr_ctx.actor_id
        assert svc.get_chapter(tr_ctx, book_slug="gift-test", chapter_id="ch02")["claimed_by"] == tr_ctx.actor_id

        # Backdate expiry in DB -> expired claim must not leak as active
        past_ts = time.time() - 60.0
        with store._lock:
            store._exec(
                "UPDATE chapters SET claim_expires_at = ? WHERE book_slug = ? AND chapter_id = ?",
                (past_ts, "gift-test", "ch02"),
            )

        bk_exp = svc.get_book(tr_ctx, book_slug="gift-test")
        ch02_exp = next(c for c in bk_exp["chapters"] if c["chapter_id"] == "ch02")
        assert ch02_exp["claimed_by"] == ""
        assert ch02_exp["claim_expires_at"] == 0

        ch_detail_exp = svc.get_chapter(tr_ctx, book_slug="gift-test", chapter_id="ch02")
        assert ch_detail_exp["claimed_by"] == ""
        assert ch_detail_exp["claim_expires_at"] == 0
    finally:
        store.close()


def test_review_client_raises_on_exact_400_boundary(tmp_path: Path) -> None:
    cfg, store, _, _, _ = _setup_env(tmp_path, None)
    try:
        rc = ReviewServerClient(cfg)

        class _Resp400:
            status_code = 400
            text = "bad request"

            def json(self) -> dict[str, Any]:
                return {"error": "bad request"}

        class _FakeClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def get(self, *args: Any, **kwargs: Any) -> _Resp400:
                return _Resp400()

            def post(self, *args: Any, **kwargs: Any) -> _Resp400:
                return _Resp400()

        with patch("scripts.mcp_server.review_client.httpx.Client", _FakeClient):
            with pytest.raises(ReviewServerError) as get_err:
                rc.get_workstream(workstream_id="ws-1")
            assert get_err.value.status_code == 400
            with pytest.raises(ReviewServerError) as post_err:
                rc.create_workstream(title="t")
            assert post_err.value.status_code == 400
    finally:
        store.close()
