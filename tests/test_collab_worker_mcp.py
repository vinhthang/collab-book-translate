"""TDD tests for Task 4 (REQ-043): collab_worker.py --mcp-url mode and plugin v3.0.0 docs."""

from __future__ import annotations

import json
from pathlib import Path
from starlette.testclient import TestClient

import scripts.collab_worker as worker_mod
from scripts.mcp_server.app import create_app
from tests.test_mcp_service_and_app import _build_env


def test_ac_043_1_worker_mcp_mode_anonymous_and_agent_key_flows(tmp_path: Path, monkeypatch):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nQuiet river.\n</original>\n</segment>\n'
    tr_xml = (
        '<segment id="S-0001" type="paragraph">\n<original>\nQuiet river.\n</original>\n'
        '<translate>\nDòng sông êm đềm trôi lững lờ.\n</translate>\n</segment>\n'
    )
    store.upsert_book(
        slug="river-book",
        title="River Book",
        author="Author",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org/pd",
        status="active",
        glossary_sha="d" * 64,
        book_record={"terms": {"river": "dòng sông"}},
        manifest={"slug": "river-book", "source_lang": "en", "target_lang": "vi"},
        proposed_by="admin",
    )
    store.upsert_chapter(
        book_slug="river-book",
        chapter_id="ch02",
        chapter_num=2,
        title="Chapter 2",
        source_xml=src_xml,
        status="open",
    )

    app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        monkeypatch.setattr(worker_mod, "_get_mcp_http_client", lambda: client)

        # 1. ANONYMOUS list-tasks & get-chapter (accepts /mcp suffix transparently)
        tasks = worker_mod.run_mcp_command(
            subcommand="list-tasks",
            mcp_url="http://testserver/mcp",
            role="translator",
            book_slug="river-book",
        )
        assert tasks["success"] is True
        assert len(tasks["open_chapters"]) == 1

        ch_data = worker_mod.run_mcp_command(
            subcommand="get-chapter",
            mcp_url="http://testserver/mcp",
            book_slug="river-book",
            chapter="ch02",
        )
        assert ch_data["success"] is True
        assert "Quiet river." in ch_data["source_xml"]

        # 2. Self-serve register-agent -> agent_key
        reg = worker_mod.run_mcp_command(
            subcommand="register-agent",
            mcp_url="http://testserver",
            handle="worker-cli-bot",
            display_name="Worker Bot",
            model_info="gemini",
        )
        assert reg["success"] is True
        agent_key = reg["agent_key"]

        # 3. Round-1 BLOCK -> Round-2 PASS with --agent-key & --rebuttals-file
        rs_client.next_round_results.append(
            {
                "verdict": "BLOCK",
                "stageStatus": "REMEDIATION_REQUIRED",
                "roundNumber": 1,
                "p0Count": 0,
                "p1Count": 1,
                "p2Count": 0,
                "judgeSummary": "Check phrasing",
                "openFindings": [{"findingKey": "F1", "severity": "P1", "symptom": "Minor", "fixRecommendation": "Fix"}],
            }
        )
        tr_file = tmp_path / "ch02.xml"
        tr_file.write_text(tr_xml, encoding="utf-8")

        sub_r1 = worker_mod.run_mcp_command(
            subcommand="submit-translation",
            mcp_url="http://testserver/mcp",
            agent_key=agent_key,
            book_slug="river-book",
            chapter="ch02",
            translation_file=str(tr_file),
            engine_model="gemini",
        )
        assert sub_r1["success"] is True
        sub_id = sub_r1["submission"]["id"]

        rebuttals_file = tmp_path / "rebuttals.json"
        rebuttals_file.write_text(
            json.dumps([{"findingKey": "F1", "rationale": "Canonical term used"}]),
            encoding="utf-8",
        )
        rs_client.next_round_results.append(
            {
                "verdict": "PASS",
                "stageStatus": "GOVERNANCE_CLEAN",
                "roundNumber": 2,
                "p0Count": 0,
                "p1Count": 0,
                "p2Count": 0,
                "judgeSummary": "Approved",
                "openFindings": [],
            }
        )
        sub_r2 = worker_mod.run_mcp_command(
            subcommand="submit-translation",
            mcp_url="http://testserver/mcp",
            agent_key=agent_key,
            book_slug="river-book",
            chapter="ch02",
            translation_file=str(tr_file),
            submission_id=sub_id,
            rebuttals_file=str(rebuttals_file),
            engine_model="gemini",
        )
        assert sub_r2["success"] is True
        assert sub_r2["submission"]["status"] == "accepted"

    store.close()


def test_ac_043_2_worker_missing_target_or_http_error_exits_nonzero(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("COLLAB_MCP_URL", raising=False)
    res_missing = worker_mod.resolve_worker_mode(gh_user=None, mcp_url=None)
    assert res_missing["mode"] == "error"

    cfg, store, _, svc = _build_env(tmp_path)
    app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        monkeypatch.setattr(worker_mod, "_get_mcp_http_client", lambda: client)
        bad = worker_mod.run_mcp_command(
            subcommand="get-chapter",
            mcp_url="http://testserver/mcp",
            book_slug="missing-book",
            chapter="ch99",
        )
        assert bad["success"] is False
        assert bad["status_code"] == 404
    store.close()


def test_ac_043_1_plugin_v3_manifest_and_mcp_docs():
    repo_root = Path(__file__).resolve().parent.parent
    plugin = json.loads((repo_root / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == "4.2.0"

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    skill = (repo_root / "skills" / "collab-translate-book" / "SKILL.md").read_text(encoding="utf-8")
    assert "https://collab.vinhthang.dev/mcp" in readme
    assert "ANONYMOUS" in readme
    assert "vinhthang/collab-books" in readme
    assert "https://collab.vinhthang.dev/mcp" in skill
