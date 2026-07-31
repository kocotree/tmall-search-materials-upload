---
name: upload-search-materials
description: Use for starting, configuring, testing, resuming, reviewing, auditing, dry-running, approving, or uploading Tmall search-recommendation materials. Always open or resume the interaction UI before requesting store names, image-source roots, product choices, media decisions, slot edits, copy, approval, or production confirmation in chat.
---
# Upload Search Materials — discovery entry

This is the repository discovery entry. The canonical instructions are:

`../../../upload-search-materials/SKILL.md`

Before taking any workflow action:

1. Resolve that path relative to this file and read the canonical `SKILL.md` completely.
2. If it cannot be resolved or read, stop with `SKILL_CANONICAL_NOT_FOUND`; do not reconstruct the workflow from docs or chat history.
3. Follow the canonical frontend-first startup contract. Missing store, image roots, NAS access, or login are not startup blockers.
4. On Windows, run the managed workbench launcher with host-approved desktop permission, then verify launcher/service SID and login-session equality. `remote_drive_letters` is diagnostic-only: saved image sources are setup-page prefill suggestions and must never become startup requirements. Validate only the sources currently shown on the setup page when the user submits them. A healthy service under the Codex sandbox identity is not valid for local-resource work: stop that exact session service and relaunch it with desktop permission before the user uses the folder picker or loads images. Image buttons then request a one-shot executor in that same desktop identity; users must not be asked to open PowerShell. Windows and macOS use the same source_id/relative_path protocol.
5. Use the managed UI launcher documented by the canonical Skill. Codex chat is only a reason-coded fallback after a real frontend failure.

Generated from canonical SHA-256:
`a0365f7de6dee88afbcb9df22d7a5778eff433d4fc2251a4a1c84c9e5249caf8`
