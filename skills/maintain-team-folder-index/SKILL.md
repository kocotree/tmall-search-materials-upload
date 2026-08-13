---
name: maintain-team-folder-index
description: Maintain decentralized, reusable folder-index snapshots for team material sources. Use when checking, syncing, incrementally refreshing, or explicitly publishing a shared Tmall search-material folder index, including when the fixed NAS share must first be mounted through the operating system.
---

# Maintain Team Folder Index

Use the repository CLI as the deterministic implementation. An ordinary upload workflow may automatically publish the first immutable snapshot for a configured source when no valid shared snapshot exists.

## Fixed team location

- macOS path: `/Volumes/浙江酷趣/天猫部/搜推素材索引-虾米`
- NAS source ID: `zhejiang-kuqu`
- Canonical share: `\\192.168.110.20\浙江酷趣\天猫部\搜推素材索引-虾米`

Read [references/protocol.md](references/protocol.md) when diagnosing snapshot validation, conflicts, cache fallback, or portability.

## Workflow

1. Load `config/local-paths.json` and confirm `team_folder_index_root` and the local `image_sources` bindings.
2. Check the team index without changing it:

   `tmall-materials team-folder-index status --config config/local-paths.json`

3. If the shared path is unavailable while an upload workflow needs the team index, automatically invoke the existing OS-owned SMB connection flow:

   `tmall-materials nas-prepare --config config/nas-sources.yaml --source-id zhejiang-kuqu --allow-mount`

   Never pass, request, log, or store NAS credentials. Do not use a silent low-level mount command.

4. By default, sync the latest valid snapshots into the local cache and rematch them against the current product table:

   `tmall-materials team-folder-index sync --config config/local-paths.json`

   If NAS is unavailable, this command may use the last verified local snapshot cache. Report that fallback explicitly.

5. Existing valid team snapshots are preferred and never refreshed automatically. If a configured source has no valid shared snapshot, automatically build its directory-only index and publish the first immutable snapshot without requesting a second authorization.

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
- Sync validates schema and SHA-256 before caching or using a snapshot.
- Missing or mismatched local bindings are reported and skipped; never guess a drive letter or mount path.
- Do not automatically delete old snapshots, scan image contents, upload materials, or change approval boundaries.
