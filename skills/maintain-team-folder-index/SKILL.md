---
name: maintain-team-folder-index
description: Maintain decentralized, reusable folder-index snapshots for team material sources. Use when checking, syncing, incrementally refreshing, or explicitly publishing a shared Tmall search-material folder index selected in the machine-local configuration.
---

# Maintain Team Folder Index

Use the repository CLI as the deterministic implementation. An ordinary upload workflow must automatically publish the first immutable snapshot for a configured source when no valid shared snapshot exists, without requesting a second verbal authorization.

## Default team location

- NAS source ID: `zhejiang-kuqu`
- Canonical share: `\\192.168.110.20\浙江酷趣\天猫部\搜推素材索引-虾米`
- A machine-local `team_folder_index_root` selected on the setup page takes precedence over this default suggestion unless an explicit environment override is present.

Read [references/protocol.md](references/protocol.md) when diagnosing snapshot validation, conflicts, cache fallback, or portability.

## Workflow

1. Load `config/local-paths.json` and confirm `team_folder_index_root` and the local `image_sources` bindings.
2. Check the team index without changing it:

   `tmall-materials team-folder-index status --config config/local-paths.json`

3. If the configured path is unavailable while an upload workflow needs the team index, keep the current product-selection handoff blocked and direct the user to choose an accessible team index folder on the setup page. Saving it refreshes the managed processor runtime and retries the same handoff once.

   Never pass, request, log, or store NAS credentials. Windows owns any required authentication; do not invoke a silent low-level mount command.

4. By default, sync the latest valid folder snapshots into the local cache:

   `tmall-materials team-folder-index sync --config config/local-paths.json`

   If NAS is unavailable, this command may use the last verified local snapshot cache. Report that fallback explicitly. Product matching is not persisted here: each upload task rematches the cached folder metadata against its own current product snapshot and matcher version.

5. Existing valid team snapshots are preferred and never refreshed automatically. If the current path-derived source ID has no snapshot but an older source ID has the same canonical path identity key, copy the newest valid portable metadata into a new immutable snapshot under the current ID and keep every old snapshot. Only when no equivalent snapshot exists should the workflow build the directory-only index and publish the first immutable snapshot without requesting a second authorization.

Directory enumeration uses a 60-second no-progress timeout so large SMB/NAS directories can return their first entry without being treated as stalled. A timeout still produces a partial result that must not be published; refresh the same local index after NAS access recovers.

## Explicit incremental update

Only enter this flow when the user explicitly asks to update a named source that already has a valid snapshot.

1. Resolve the requested `source_id` and its local root from `image_sources`.
2. Refresh that source or a precise relative subtree with the existing `index-folders --refresh-source` or `--refresh-prefix` command. Never broaden a subtree request into a full-source scan without confirmation.
3. Show the refresh summary and any partial failures. Do not publish an incomplete or empty source.
4. Ask for or rely on the user's already explicit instruction to publish, then run:

   `tmall-materials team-folder-index publish --config config/local-paths.json --source-id SOURCE_ID`

5. Run status again and report the new immutable snapshot ID. Never modify or delete another snapshot.

## Boundaries

- Any authorized team machine may publish; there is no maintainer machine.
- Publishing uses a short per-source lease and creates a new snapshot plus a current pointer.
- Shared snapshots contain only `source_id + relative_path` folder metadata, never machine-specific absolute paths.
- Sync validates schema and SHA-256, then compares the manifest canonical path identity key with the current local binding identity key before caching or using a snapshot. The binding key uses `canonical_unc` when provided and otherwise uses the local `path`, so `canonical_unc` remains optional.
- The reusable cache contains only validated folder snapshots. `folder-candidates.csv` is generated inside the current task and must never be reused by another task or Plugin version.
- Missing or mismatched local bindings are reported and skipped; never guess a drive letter or mount path.
- Do not automatically delete old snapshots, scan image contents, upload materials, or change approval boundaries.
