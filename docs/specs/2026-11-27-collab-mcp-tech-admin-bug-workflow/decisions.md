# Decisions: Dedicated `tech_admin` Role & Hybrid Bug Resolution Workflow (`v4.6.0`)

Governing specification: [spec.md](./spec.md)

## ADR-042: Dedicated `tech_admin` Role + Hybrid MCP Queue & Non-Blocking Webhook/GitHub Issue Dispatch

- Governs: REQ-076, REQ-077, REQ-078
- State: Accepted
- Context and constraints: Previously `triage_bug_report(status="accepted")` only persisted a DB status and left bugs mixed into `agent_admin`'s literary queue without ownership locks, PR tracking, or an active engineering trigger. Sharing `agent_admin` with bug-fixing agents also violates least privilege by granting book-parsing and chapter-approval authority to code-fix bots, whereas blocking the Admin UI HTTP request on external webhook/GitHub network calls risks user-facing timeouts.
- Viable options:
  1. Introduce a separated `tech_admin` role + `bug_reports` claim/PR/issue columns + `claim_bug_report` lock, paired with a hybrid `Accept` flow that both exposes `accepted_bug_reports` in `list_tasks(role="tech_admin")` AND dispatches non-blocking optional outbound webhook + optional GitHub Issue creation.
  2. Reuse `agent_admin` and poll `open_bug_reports` without claims or outbound triggers.
  3. Trigger synchronous subprocess/Git commits directly inside the `collab-mcp` pod on **Accept**.
- Decision: Choose Option 1 (`tech_admin` role + claim lock + hybrid non-blocking webhook/GitHub dispatch + PR/branch workflow).
- Costs and risks: Requires idempotent SQLite/PostgreSQL schema migrations on `bug_reports` (`claimed_by`, `claimed_at`, `issue_url`, `pr_url`, `dispatch_status`) and upgrading the `/admin` Agents role toggle from a 2-state button to a 3-role `<select>`. Network errors on optional webhook/GitHub calls are captured into `dispatch_status` instead of aborting triage so `/admin#bugs` remains responsive.
- Rejected alternatives: Option 2 violates role isolation (`A1`) and lacks a push trigger (`A2`); Option 3 mixes runtime MCP hosting with arbitrary git mutation/secret exposure inside the cluster pod (`A3`).
- Affected components: `scripts/mcp_server/{config,db,auth,service,app,admin_ui}.py`, `scripts/collab_worker.py`, `agents/{collab_tech_admin,collab_admin}.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json`, `.github/workflows/ci-cd.yml`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{config,db,auth,service,app,admin_ui}.py`, `scripts/collab_worker.py`, and `agents/collab_admin.md` (`b1c5e80`). |
| Attributable intent and explicit scope | PASS | Directly grounded in `/grill-me` choices `A1`–`A3`; non-goals enforce least privilege and prevent unsafe in-pod git mutation. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-076..REQ-078` define exact status codes (`200`/`400`/`403`/`404`/`409`/`422`), column migrations, webhook/GitHub contracts, UI checks (`node --check`), and `"4.6.0"` metadata. |
| Assumptions and owner-deferred questions are explicit | PASS | Opt-in webhook/GitHub env vars default safely; zero `[PROVISIONAL]` items remain. |
| Architectural rationale respects requirements | PASS | `ADR-042` governs `REQ-076..REQ-078` cleanly. |
| IDs, references, and revision route are consistent | PASS | Uses `REQ-076..REQ-078` and `ADR-042` above `REQ-075`/`ADR-041`, targeting `REQ-059`, `REQ-060`, and `REQ-075`. |
