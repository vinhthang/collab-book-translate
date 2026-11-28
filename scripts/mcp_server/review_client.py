"""Server-to-server HTTP client for https://review.vinhthang.dev (TRANSLATION_REVIEW & /api/v1/translate)."""

from __future__ import annotations

from typing import Any, Optional
import httpx

from scripts.mcp_server.config import McpServerConfig


class ReviewServerError(RuntimeError):
    """Raised when review.vinhthang.dev returns a non-2xx error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


_TRANSLATION_REVIEW_AGENTS: tuple[str, ...] = (
    "vi-translation-fidelity",
    "vi-literary-fluency",
    "vi-terminology-pronoun-hawk",
    "skeptic-judge",
)


class ReviewServerClient:
    """Client driving TRANSLATION_REVIEW workstreams and fallback chapter translation."""

    def __init__(self, cfg: McpServerConfig, timeout_seconds: float = 480.0) -> None:
        self._base_url = cfg.review_server_url.rstrip("/")
        self._token = cfg.review_bearer_token
        self._timeout = timeout_seconds
        self._review_engine = cfg.review_engine
        self._agent_slugs = list(cfg.review_agent_slugs)

    def _headers(self) -> dict[str, str]:
        hdrs = {"Content-Type": "application/json"}
        if self._token:
            hdrs["Authorization"] = f"Bearer {self._token}"
        return hdrs

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers())
        if resp.status_code >= 400:
            try:
                err_msg = resp.json().get("error") or resp.text
            except Exception:
                err_msg = resp.text
            raise ReviewServerError(
                f"review-server GET {path} failed ({resp.status_code}): {err_msg}",
                status_code=resp.status_code,
            )
        return resp.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            try:
                err_msg = resp.json().get("error") or resp.text
            except Exception:
                err_msg = resp.text
            raise ReviewServerError(
                f"review-server {path} failed ({resp.status_code}): {err_msg}",
                status_code=resp.status_code,
            )
        return resp.json()

    def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
        data = self._post(
            "/api/v1/workstreams",
            {
                "title": title[:250],
                "initialStage": "TRANSLATION_REVIEW",
                "maxRounds": max_rounds,
            },
        )
        return data.get("workstream") or data

    def run_translation_round(
        self,
        *,
        workstream_id: str,
        content: str,
        rebuttals: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        use_manual = bool(self._agent_slugs)
        payload: dict[str, Any] = {
            "stage": "TRANSLATION_REVIEW",
            "selectionMode": "manual" if use_manual else "auto",
            "discoverRepoRules": False,
            "content": content,
        }
        if use_manual:
            payload["agentSlugs"] = list(self._agent_slugs)
        if self._review_engine:
            target_slugs = (*self._agent_slugs, "skeptic-judge") if use_manual else _TRANSLATION_REVIEW_AGENTS
            payload["engineOverrides"] = {
                slug: self._review_engine for slug in target_slugs
            }
        if rebuttals:
            payload["rebuttals"] = rebuttals

        data = self._post(f"/api/v1/workstreams/{workstream_id}/rounds", payload)
        ws = data.get("workstream") or {}
        rnd = data.get("round") or {}
        ledger = data.get("ledger") or []
        open_statuses = {"OPEN", "PERSISTENT", "NEW_REGRESSION"}
        open_findings = [
            {
                "findingKey": item.get("findingKey", ""),
                "severity": item.get("severity", "P1"),
                "symptom": item.get("symptom", ""),
                "fixRecommendation": item.get("fixRecommendation", ""),
                "status": item.get("status", "OPEN"),
            }
            for item in ledger
            if isinstance(item, dict) and item.get("status") in open_statuses
        ]
        return {
            "verdict": str(rnd.get("verdict") or "BLOCK"),
            "stageStatus": str(ws.get("stageStatus") or "REMEDIATION_REQUIRED"),
            "roundNumber": int(rnd.get("roundNumber") or ws.get("roundNumber") or 1),
            "p0Count": int(rnd.get("p0Count") or 0),
            "p1Count": int(rnd.get("p1Count") or 0),
            "p2Count": int(rnd.get("p2Count") or 0),
            "judgeSummary": str(rnd.get("judgeSummary") or ""),
            "openFindings": open_findings,
        }

    def get_workstream(self, *, workstream_id: str) -> dict[str, Any]:
        """Fetch full workstream details, rounds, specialist critiques, and ledger items."""
        return self._get(f"/api/v1/workstreams/{workstream_id}")

    def approve_workstream(
        self,
        *,
        workstream_id: str,
        override_escalated: bool = False,
    ) -> dict[str, Any]:
        data = self._post(
            f"/api/v1/workstreams/{workstream_id}/governance",
            {"action": "approve", "overrideEscalated": override_escalated},
        )
        return data.get("workstream") or data

    def translate_chapter(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        source_lang: str,
        source_xml: str,
        book_record: dict[str, Any],
        brief: str = "",
    ) -> dict[str, Any]:
        return self._post(
            "/api/v1/translate",
            {
                "bookSlug": book_slug,
                "chapterId": chapter_id,
                "sourceLang": source_lang,
                "targetLang": "vi",
                "sourceXml": source_xml,
                "bookRecord": book_record,
                "brief": brief,
                "engine": "gemini",
            },
        )
