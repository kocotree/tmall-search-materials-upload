# Implementation Traceability

## Live-test problem matrix

| # | Problem | Requirement / tasks | Reused implementation | Regression / acceptance evidence |
|---|---|---|---|---|
| 1 | Popup handling efficiency | Popup outcome verification, 9.1–9.8 | `browser/material_page.py` maintained high-value collector | `test_browser_pages.py`; sanitized replay; fresh dry-run |
| 2 | Attempt/checkpoint isolation | Attempt-isolated collection, 7.1–7.9 | `collection_worker.py`, `setup_collection.py`, `supplement_collection.py` | Worker/setup/supplement tests; session replay |
| 3 | Unified recovery state | Recovery resolver, 3.1–3.7 and 7.7–7.8 | `collection_runtime.py`, `interaction/session.py`, `interaction/web.py` | Session/web/runtime tests; duplicate “已提交” |
| 4 | Windows launcher/worker identity | Windows ownership, 8.1–8.6 | `collection_worker.py` | `test_collection_worker.py`; short writable basetemp |
| 5 | Click outcome verification | Popup outcome verification, 9.1–9.7 | `browser/material_page.py` | swallowed-click, no-op, pagination tests |
| 6 | Canonical runtime environment | Runtime resolver, 6.1–6.6 | `runtime_config.py`, managed launcher | runtime-isolation tests; offline prepared environment |
| 7 | Progress observability | Actionable progress, 10.1–10.3 | Worker status and stage API/UI | worker/web tests; live pre-checkpoint evidence |
| 8 | Atomic persistence parent directories | Persistence contract, 5.1–5.7 | session/service/setup/worker writers | persistence and first-history tests |
| 9 | BOM/temp-dir Windows issues | Encoding and Windows test contract, 5.3–5.7 | `collection_runtime.py`, pytest basetemp | BOM, long-path and full-suite runs |
| 10 | Early input-data quality | Input quality, 10.4–10.7 | setup preflight and completeness matrix | sanitized 611/44 fixture; setup/web tests |

## Related OpenSpec changes

| Change | Relationship |
|---|---|
| `make-skill-interactions-frontend-first` | Reuse the managed UI, explicit-session rule, frontend-first routing, freeze/withdraw behavior and Skill discovery. Its fresh-context trigger tasks remain independent; this change adds wait visibility and acknowledgement recovery. |
| `stabilize-search-material-collection-workflow` | Reuse processing claims, selector readiness, maintained high-value collector, checkpoint validation and setup-to-completeness path. Remaining live tasks 8.3–8.5 are superseded by this change’s broader live acceptance; 8.6–8.7 remain completed evidence. |
| `close-high-value-collection-runtime-gaps` | Reuse worker ownership, attempt-derived status, environment fingerprint and popup state machine. Its remaining reconciliation/live tasks 7.6 and 8.3–8.11 are superseded by tasks 12.2–12.10 here. |

## Baseline

- Branch: `codex/non-ai-slot-planning`.
- Targeted pre-refactor run: 222 passed, 1 skipped.
- Command scope: interaction session/web, CLI orchestration, collection runtime/worker, setup, browser page and runtime isolation.
- The run used the prepared module environment and a short project-writable basetemp.
- Existing user changes were neither staged, reverted nor overwritten.

## Evidence policy

Only sanitized identities, counts, reason codes and relative artifact roles may be committed. Fixtures and reports must not include cookies, authentication headers, credentials, QR/SMS payloads, source image bytes, approval manifests, upload calls or publish actions.
