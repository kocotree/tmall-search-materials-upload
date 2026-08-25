# Team folder-index protocol

## Layout

```text
sources/<source_id>/
  current.json
  snapshots/<snapshot_id>/
    manifest.json
    folders.csv
```

Each publish creates a new snapshot directory. Existing snapshot directories are never overwritten. `current.json` is only a small pointer and may be replaced atomically after the snapshot is complete.

`folders.csv` contains `folder_id`, `source_id`, `relative_path`, `folder_name`, and `parent_relative_path`. It must not contain an absolute path. The manifest records source identity, row count, schema version, completion flag, publisher, time, and SHA-256 of the CSV. Snapshot selection and local binding use exact `source_id` only; `canonical_source` is retained only as an audit/display field when present and is not used for matching.

## Selection and fallback

Use the snapshot named by `current.json` when it validates. If that pointer or snapshot is invalid, inspect snapshots newest-first and use the newest valid immutable snapshot. Never repair or delete invalid shared data automatically.

On sync, copy validated files into `<folder_index_root>/team-cache`. Do not materialize or reuse a global candidate CSV. When the user submits a product selection, match the validated cached folders for the current configured source IDs against that task's product snapshot with the current matcher, derive absolute paths from the current machine's `image_sources`, and write `folder-candidates.csv` only inside that task. If the shared root is unavailable, the last validated local folder cache for the same current source IDs may be used and must be reported with origin `local_cache`. Old source IDs are kept as immutable historical data but are not compatibility aliases for new tasks.

## Concurrency

Publishing acquires `sources/<source_id>/.publish.lock` with exclusive creation and a short expiry. A live lease blocks another publisher. An expired lease may be replaced only after checking that it did not change. The lease protects pointer publication; it does not grant permission to mutate existing snapshots.

## Failure handling

- Hash, schema, or source ID mismatch: do not use that snapshot.
- Requested source has no local binding: block that requested sync or publish.
- Shared path unavailable during an upload workflow: automatically open the OS SMB connection flow; after system authentication and mount-identity validation, create the fixed index subdirectory if needed and continue.
- Configured path-derived source ID missing a valid snapshot: build a directory-only local index from the current binding and publish its first immutable snapshot. Existing valid snapshots under other source IDs are never automatically refreshed, migrated, reused, or deleted.
- Partial source refresh or empty source: do not publish.
- Publish conflict: leave both local indexes untouched, wait for the other publisher to finish, then re-check status.
