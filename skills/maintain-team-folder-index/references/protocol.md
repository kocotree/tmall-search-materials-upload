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

`folders.csv` contains `folder_id`, `source_id`, `relative_path`, `folder_name`, and `parent_relative_path`. It must not contain an absolute path. The manifest binds the canonical source identity, row count, schema version, completion flag, publisher, time, and SHA-256 of the CSV.

## Selection and fallback

Use the snapshot named by `current.json` when it validates. If that pointer or snapshot is invalid, inspect snapshots newest-first and use the newest valid immutable snapshot. Never repair or delete invalid shared data automatically.

On sync, copy validated files into `<folder_index_root>/team-cache`, then materialize `<folder_index_root>/folder-candidates.csv` with absolute paths derived from the current machine's `image_sources`. If the shared root is unavailable, the last validated local cache can be rematerialized and must be reported with origin `local_cache`.

## Concurrency

Publishing acquires `sources/<source_id>/.publish.lock` with exclusive creation and a short expiry. A live lease blocks another publisher. An expired lease may be replaced only after checking that it did not change. The lease protects pointer publication; it does not grant permission to mutate existing snapshots.

## Failure handling

- Hash, schema, source ID, or canonical-source mismatch: do not use that snapshot.
- Requested source has no local binding: block that requested sync or publish.
- Shared path unavailable during an upload workflow: automatically open the OS SMB connection flow; after system authentication and mount-identity validation, create the fixed index subdirectory if needed and continue.
- Configured source missing a valid shared snapshot: automatically build a directory-only local index and publish its first immutable snapshot. Existing valid snapshots are never automatically refreshed.
- Partial source refresh or empty source: do not publish.
- Publish conflict: leave both local indexes untouched, wait for the other publisher to finish, then re-check status.
