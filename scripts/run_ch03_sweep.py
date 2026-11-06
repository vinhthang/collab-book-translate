"""Run sweep for Chapter III PR and complete book release."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.collab_sweep import run_sweep
from scripts.evaluate import run_evaluation
from scripts.compile_book import compile_book_formats


def live_evaluate_fn(source_xml: str, trans_xml: str, book_record: Dict[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        t_dir = Path(td)
        s_p = t_dir / "source.txt"
        d_p = t_dir / "translation.xml"
        b_p = t_dir / "book_record.json"
        o_p = t_dir / "eval.json"
        s_p.write_text(source_xml, encoding="utf-8")
        d_p.write_text(trans_xml, encoding="utf-8")
        b_p.write_text(json.dumps(book_record, ensure_ascii=False, indent=2), encoding="utf-8")
        res = run_evaluation(
            source_path=str(s_p),
            draft_path=str(d_p),
            book_record_path=str(b_p),
            output_path=str(o_p),
            force_mock=False,
            engine="workbuddy",
        )
        return res


def sweep_evaluate_fn(*args, **kwargs):
    if len(args) == 2 and isinstance(args[0], dict):
        pr, root = args[0], Path(args[1])
        from scripts.collab_pr_validate import parse_pr_body
        from scripts.collab_sweep import _extract_ch_id_and_slug, read_pr_file, GitHubCLIClient
        head_ref = pr.get("head_ref") or pr.get("headRefName", "")
        body = pr.get("body", "")
        raw_chunk = parse_pr_body(body).get("chunk_id")
        ch_id, slug = _extract_ch_id_and_slug(head_ref, raw_chunk)
        s_file = root / "runs" / slug / ch_id / "source.txt"
        source_xml = s_file.read_text(encoding="utf-8")
        gh = GitHubCLIClient(repo_root=root)
        trans_xml = read_pr_file(gh, pr["number"], head_ref, f"runs/{slug}/{ch_id}/translation.xml", root)
        rec_path = root / "runs" / slug / "book_record.json"
        book_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else {}
        return live_evaluate_fn(source_xml, trans_xml, book_record)
    elif len(args) == 3:
        return live_evaluate_fn(args[0], args[1], args[2])
    else:
        return live_evaluate_fn(*args, **kwargs)


def pilot_compile_fn(root: Path, slug: str) -> List[str]:
    run_dir = root / "runs" / slug
    out_dir = root / "runs" / slug / "release_assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    res = compile_book_formats(
        run_dir=run_dir,
        output_dir=out_dir,
        formats="epub,pdf,docx",
        allow_incomplete=False,
    )
    return [str(p) for p in res.values()]


if __name__ == "__main__":
    repo_root = Path(".").resolve()
    print("[ch03 Sweep] Running run_sweep on main with window_hours=0.0, live evaluate_fn, and compile_fn...")
    sweep_res = run_sweep(
        repo_root=repo_root,
        evaluate_fn=sweep_evaluate_fn,
        compile_fn=pilot_compile_fn,
        window_hours=0.0,
        bot_login="1quachuoi9vang",
        human_approver="vinhthang",
    )
    print(f"[ch03 Sweep] Sweep completed: {sweep_res}")