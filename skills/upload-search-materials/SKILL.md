---
name: upload-search-materials
description: Use for starting, configuring, testing, resuming, reviewing, auditing, dry-running, approving, or uploading Tmall search-recommendation materials. Always open or resume the interaction UI before requesting store names, image-source roots, product choices, media decisions, slot edits, copy, approval, or production confirmation in chat.
---
# Upload Search Materials — plugin entry

This is the Plugin discovery entry. The canonical instructions are:

`../../SKILL.md`

Before taking any workflow action:

1. Resolve that path relative to this file and read the canonical `SKILL.md` completely.
2. If it cannot be resolved or read, stop with `SKILL_CANONICAL_NOT_FOUND`; do not reconstruct the workflow from docs or chat history.
3. Follow the canonical business-frontend contract. Missing store, image roots, or NAS access do not block workbench startup. Login is checked automatically: keep the business setup form gated while the visible Qianniu window needs user interaction, then continue automatically without a page-validation button.
4. On Windows, use the fixed managed workbench launcher already allowed during runtime preparation, then verify launcher/service SID and login-session equality. Do not ask for chat or command approval for normal workbench startup, exact-session status reads, handoff polling, configured-source metadata reads, candidate generation, or one-shot image loading. If the host has not yet allowed the fixed launcher, handle only that narrow launcher permission once as `SYSTEM_PERMISSION_REQUIRED`; never widen it to ad-hoc PowerShell. `remote_drive_letters` is diagnostic-only: saved image sources are setup-page prefill suggestions and must never become startup requirements. Validate only the sources currently shown on the setup page when the user submits them. A healthy service under the Codex sandbox identity is not valid for local-resource work: stop that exact session service and relaunch it through the same fixed desktop entry before the user uses the folder picker or loads images. Image buttons then request a one-shot executor in that same desktop identity; users must not be asked to open PowerShell or approve the image read a second time.
   The managed service starts a single-session background dispatcher in the same validated Windows desktop identity. The dispatcher consumes persisted handoffs and bound requests through the canonical fixed processors, including setup, product selection, final material selection, copy generation, and publish authorization. Codex does not poll or invoke those processors during a healthy normal workflow.
5. Before every user-interaction reminder, read the exact current-session workbench `url` from the managed launcher result or `ui-status` and include it as a clickable link in that same message. Never hardcode a port, guess the latest session, reuse another session's URL, or ask the user to return to the workbench without the link.
6. Before product-selection candidates are generated, follow the canonical `$maintain-team-folder-index` sync gate. Automatically publish the first immutable snapshot when a configured source has no valid shared snapshot, without asking for a second verbal authorization. Existing valid snapshots are read-only inputs; only refresh and republish of an existing source require an explicit user request naming that source. Never consume a global `folder-candidates.csv`; rematch the current task from cached `folders.csv` metadata.
7. Use the managed UI launcher documented by the canonical Skill. Codex chat is only a reason-coded fallback after a real frontend failure.
8. Workbench submissions carry the bounded authority documented by the canonical Skill. The workbench dispatcher processes normal handoffs and bound requests directly; product, folder, image, slot, copy, and publish decisions remain in the workbench. Only unexpected paths that require new technical authority may trigger a host approval request.
9. While the workbench and its declared processors are healthy, Codex must not call `agent-wait`, `listen-handoff`, `wait-agent-request`, a stage processor, or a terminal status loop. Submission is atomically persisted before the dispatcher is notified; service startup and exact-session recovery scan the durable backlog before waiting for new work, so both “先提交后恢复” and “先启动后提交” are covered without chat text such as “已提交”. The page polls the dispatcher status and shows ready, queued, running, complete, or failed. Do not search or read project source, enumerate Plugin/version/runs directories, or directly read handoff/input files to rediscover a normal route. Source diagnosis is allowed only after a fixed processor returns a stable error and writes the current Agent diagnostic; read `diagnose-session` first and keep investigation within that failing processor's call path.

Generated from canonical SHA-256:
`a2306307cf0318b31e2c35d9572e47ecf28de6132bc0960b92ddbfefc6da6b6e`
