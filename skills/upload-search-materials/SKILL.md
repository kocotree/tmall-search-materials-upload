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
4. On Windows, run the managed workbench launcher with host-approved desktop permission, then verify launcher/service SID and login-session equality. `remote_drive_letters` is diagnostic-only: saved image sources are setup-page prefill suggestions and must never become startup requirements. Validate only the sources currently shown on the setup page when the user submits them. A healthy service under the Codex sandbox identity is not valid for local-resource work: stop that exact session service and relaunch it with desktop permission before the user uses the folder picker or loads images. Image buttons then request a one-shot executor in that same desktop identity; users must not be asked to open PowerShell. Windows and macOS use the same source_id/relative_path protocol.
5. Before product-selection candidates are consumed, follow the canonical `$maintain-team-folder-index` sync gate. Existing valid snapshots are read-only inputs; refresh and publish require an explicit user request for a named source.
6. Use the managed UI launcher documented by the canonical Skill. Codex chat is only a reason-coded fallback after a real frontend failure.

Generated from canonical SHA-256:
`89c058abc769b98bc8c9fc6286c26423f4c352ec3443251b3612ee7c7c03ee56`
