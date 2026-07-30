# Sanitized Live Failure Evidence

## Source

- Codex task: `019fb076-5825-75e0-9ff4-c41fda1773e0`
- Managed UI session: `20260730_004051`
- Evidence is sanitized; it contains no credentials, cookies, QR/SMS data, NAS secrets, directory listings, image contents, approvals, uploads, or publish actions.

## Observed Persistence Sequence

1. The first setup draft wrote current input revision 1.
2. It wrote `revisions/0001/input.json`.
3. Replacing `session.json` failed with Windows `WinError 5`.
4. `session.json` remained setup revision 0 and `draft`.
5. The automatic draft retry returned HTTP 409.
6. Every later setup submit returned HTTP 409 because revision 1's snapshot already existed.
7. No handoff, processing claim, collection, dry-run, approval, upload, or publish was created.

## Observed Wait Sequence

- Ten `agent_wait_created` events were recorded for the same session, stage, claimant, and expected revision.
- No renew or explicit clear event was recorded.
- Each new wait exposed another 90-second expiry, while the Agent used a five-minute setup budget.
- The authoritative session therefore stayed `draft`, so the waiter never observed a handoff and the page continued to display listening.

## Observed Windows Identity Difference

- The configured mapped sources were unavailable in the Codex sandbox identity.
- The same three configured directories were available in the authorized desktop user context.
- Only metadata reachability was checked; no directory enumeration or source-file read occurred.

## Baseline

Before this change, the targeted session, persistence, wait, service, path-diagnostic, folder-picker, and interaction-web suites completed with:

`219 passed, 1 skipped`

The skipped test is platform/interactive-context dependent and is not treated as product acceptance.

## Change Coordination

- `harden-handoff-and-live-workflow-reliability` remains the source of the existing handoff/claim/attempt model and its unrelated live collection acceptance remains open.
- Its fresh wait-lease acceptance task 12.2 is superseded by this change's shorter lease, single-wait renewal, explicit cleanup, and fresh-session acceptance.
- `fix-nas-path-detection-and-folder-picker` remains the source of reason-coded path diagnostics and native helper protocol.
- Its local picker live task 6.5 is superseded by this change's desktop-identity and proven-window-visible acceptance.
- Its real UNC access task 6.6 remains independent because this change does not require or synthesize NAS credentials.
