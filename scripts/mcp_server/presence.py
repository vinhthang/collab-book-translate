"""Live MCP connection tracking and time-window presence computation for registered agents."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from scripts.mcp_server.db import parse_utc_timestamp

ONLINE_WINDOW_SECONDS = 300
IDLE_WINDOW_SECONDS = 1800

CONNECTION_STATE_RANK = {
    "connected": 0,
    "online": 1,
    "idle": 2,
    "offline": 3,
    "never": 4,
    "revoked": 5,
}


def format_relative_time(seconds_ago: Optional[int]) -> str:
    """Format an elapsed-second offset into a concise human-readable label."""
    if seconds_ago is None:
        return "Never"
    sec = max(0, int(seconds_ago))
    if sec < 15:
        return "Just now"
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


class AgentPresenceTracker:
    """Thread-safe in-memory tracker for active persistent /sse agent streams."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: dict[str, dict[str, dict[str, Any]]] = {}

    def open_connection(self, handle: str, conn_id: str, *, client_name: str = "") -> int:
        clean_h = (handle or "").strip().lower()
        clean_id = (conn_id or "").strip()
        if not clean_h or not clean_id:
            return 0
        with self._lock:
            bucket = self._conns.setdefault(clean_h, {})
            bucket[clean_id] = {"opened_at": time.time(), "client_name": client_name}
            return len(bucket)

    def close_connection(self, handle: str, conn_id: str) -> int:
        clean_h = (handle or "").strip().lower()
        clean_id = (conn_id or "").strip()
        if not clean_h or not clean_id:
            return 0
        with self._lock:
            bucket = self._conns.get(clean_h)
            if not bucket:
                return 0
            bucket.pop(clean_id, None)
            if not bucket:
                self._conns.pop(clean_h, None)
                return 0
            return len(bucket)

    def active_connection_count(self, handle: str) -> int:
        clean_h = (handle or "").strip().lower()
        if not clean_h:
            return 0
        with self._lock:
            return len(self._conns.get(clean_h) or {})


def compute_agent_presence(
    agent: dict[str, Any],
    *,
    live_connections: int = 0,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Compute live connection_state, relative time, and sort rank for one agent row."""
    out = dict(agent)
    eff_now = time.time() if now is None else float(now)
    revoked = bool(str(out.get("revoked_at") or "").strip())
    seen_ts = parse_utc_timestamp(str(out.get("last_seen_at") or ""))
    delta: Optional[int] = max(0, int(eff_now - seen_ts)) if seen_ts is not None else None
    live_cnt = max(0, int(live_connections))

    if revoked:
        state = "revoked"
    elif live_cnt > 0:
        state = "connected"
    elif delta is None:
        state = "never"
    elif delta <= ONLINE_WINDOW_SECONDS:
        state = "online"
    elif delta <= IDLE_WINDOW_SECONDS:
        state = "idle"
    else:
        state = "offline"

    out["live_connections"] = live_cnt
    out["connection_state"] = state
    out["is_online"] = state in {"connected", "online"}
    out["last_seen_seconds_ago"] = delta
    out["relative_last_seen"] = "Never connected" if delta is None else format_relative_time(delta)
    out["presence_rank"] = CONNECTION_STATE_RANK.get(state, 9)
    return out


def enrich_and_sort_agents(
    agents: list[dict[str, Any]],
    *,
    tracker: Optional[AgentPresenceTracker] = None,
    now: Optional[float] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Enrich agents with presence metadata, sort presence-first, and compute summary counts."""
    eff_now = time.time() if now is None else float(now)
    enriched: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "connected": 0,
        "online": 0,
        "idle": 0,
        "offline": 0,
        "never": 0,
        "revoked": 0,
        "active_online": 0,
    }
    for row in agents:
        handle = str(row.get("handle") or "")
        live_cnt = tracker.active_connection_count(handle) if tracker is not None else 0
        item = compute_agent_presence(row, live_connections=live_cnt, now=eff_now)
        enriched.append(item)
        counts["total"] += 1
        st = item["connection_state"]
        if st in counts:
            counts[st] += 1
        if item["is_online"]:
            counts["active_online"] += 1

    enriched.sort(
        key=lambda a: (
            int(a["presence_rank"]),
            int(a["last_seen_seconds_ago"]) if a["last_seen_seconds_ago"] is not None else 10**9,
            str(a.get("display_name") or a.get("handle") or "").lower(),
        )
    )
    return enriched, counts
