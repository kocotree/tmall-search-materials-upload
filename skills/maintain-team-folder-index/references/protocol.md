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

`folders.csv` contains `folder_id`, `source_id`, `relative_path`, `folder_name`, and `parent_relative_path`. It must not contain an absolute path. The manifest binds the canonical source identity, row count, schema version, completion flag, publisher, time, and SHA-256 of the CSV. Identity comparison uses the normalized path after removing a drive letter or UNC server component; a local binding uses `canonical_unc` when present and otherwise uses `path`.

## Selection and fallback

Use the snapshot named by `current.json` when it validates. If that pointer or snapshot is invalid, inspect snapshots newest-first and use the newest valid immutable snapshot. Never repair or delete invalid shared data automatically.

On sync, copy validated files into `<folder_index_root>/team-cache`. Do not materialize or reuse a global candidate CSV. When the user submits a product selection, match the validated cached folders against that task's product snapshot with the current matcher, derive absolute paths from the current machine's `image_sources`, and write `folder-candidates.csv` only inside that task. If the shared root is unavailable, the last validated local folder cache may be used and must be reported with origin `local_cache`. When an old source ID and current path-derived ID have the same path identity key, publish the newest valid old portable snapshot under the new ID and retain the old immutable data; old IDs remain usable as compatibility aliases for existing tasks.

## Concurrency

Publishing acquires `sources/<source_id>/.publish.lock` with exclusive creation and a short expiry. A live lease blocks another publisher. An expired lease may be replaced only after checking that it did not change. The lease protects pointer publication; it does not grant permission to mutate existing snapshots.

## Failure handling

- Hash, schema, source ID, or canonical/local path identity-key mismatch: do not use that snapshot.
- Requested source has no local binding: block that requested sync or publish.
- Shared path unavailable during an upload workflow: automatically open the OS SMB connection flow; after system authentication and mount-identity validation, create the fixed index subdirectory if needed and continue.
- Configured path-derived source ID missing a valid snapshot: migrate the newest equivalent legacy-ID snapshot when available; otherwise build a directory-only local index and publish its first immutable snapshot. Existing valid snapshots are never automatically refreshed or deleted.
- Partial source refresh or empty source: do not publish.
- Publish conflict: leave both local indexes untouched, wait for the other publisher to finish, then re-check status.
