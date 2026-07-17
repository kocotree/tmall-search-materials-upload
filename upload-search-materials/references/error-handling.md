# Error Handling

| Code | Meaning | Default action |
| --- | --- | --- |
| `DATA_SCHEMA` | Missing or changed CSV columns | Block the batch |
| `MISSING_PRODUCT_ID` | Product has no remote ID | Skip upload; manual review |
| `DUPLICATE_PRODUCT_ID` | Multiple source records share an ID | Manual review |
| `RULE_CONFLICT` | Monthly sources disagree | Manual review |
| `ASSET_NOT_FOUND` | No associated media directory | Manual review |
| `ASSET_INVALID` | Count, ratio, size, type, or readability failure | Fix locally; do not upload |
| `LICENSE_UNKNOWN` | Media usage rights not established | Block upload |
| `PRODUCT_MISMATCH` | UI name/ID differs from task | Stop immediately |
| `AUTH_EXPIRED` | Login or authorization expired | Stop batch; ask user to sign in |
| `HUMAN_CHECK` | CAPTCHA, QR, SMS, or risk control | Stop; require user action |
| `UPLOAD_REJECTED` | UI rejects a file or field | Record exact message; manual review |
| `PUBLISH_UNCERTAIN` | No trustworthy success/failure signal | Do not retry; verify remotely |
| `MODERATION_FAILED` | Platform review failed | Record reason; manual review |

Retry only transient page/network failures, at most twice, before the final publish action. Never automatically retry an uncertain publish because it can create duplicates. Use `task_id + media fingerprints + existing remote material ID` as the idempotency evidence.
