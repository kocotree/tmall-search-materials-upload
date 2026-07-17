# Data Schema

## Product source

Required columns:

| Column | Purpose | Blocking condition |
| --- | --- | --- |
| `商品ID` | Unique remote product key | Blank, non-numeric, or duplicated |
| `商品名称（查找引用）` | Review label | Blank |
| `货号（查找引用）` | Asset fallback key | Blank when no product-ID directory exists |
| `产品等级` | Monthly eligibility | Blank or unknown value |
| `链接` | Product verification | Blank before publishing |
| `运营` | Task owner | Blank |
| `组别` | Reporting group | Blank |
| `品类-公司维度划分` | Monthly rule join key | Blank or unmatched |

Treat `商品ID` as text to avoid numeric formatting and precision changes.

## Monthly rule source

Use the merged rule table with columns `月份`, `品类`, and `要推等级`. Normalize Chinese and ASCII commas, trim spaces, and compare exact normalized values.

## Asset layout

Prefer:

```text
asset-root/
  <商品ID>/
    images/
      01.jpg
      02.jpg
      03.jpg
    videos/
      01.mp4
    metadata.json
```

Allow a `货号` directory only as a fallback. Never fuzzy-match on product name without human confirmation.

## Task fields

Each task must include `task_id`, product identifiers, owner, month, category, grade, asset directory, image/video counts, desired slot count, status, reason, confirmation identity/time, attempt count, remote material ID, evidence, and timestamps.

Allowed initial states are `pending_validation`, `ready_for_review`, `needs_manual_review`, `skipped`, and `blocked`.
