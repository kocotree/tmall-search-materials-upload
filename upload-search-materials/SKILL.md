---
name: upload-search-materials
description: Use when preparing, validating, reviewing, or uploading Tmall search-recommendation product materials from CSV product data and local, NAS, or Feishu asset sources, including 3-slot or 9-slot image/video material tasks and upload result reports.
---

# Upload Search Materials

## Overview

Turn product tables and media folders into traceable Tmall search-recommendation upload tasks. Default to dry-run validation and require an explicit human confirmation immediately before publishing.

## Workflow

1. Identify the product table, monthly rules table, month, asset root, store, and output directory.
2. Read [references/business-rules.md](references/business-rules.md) before selecting products.
3. Read [references/data-schema.md](references/data-schema.md) before parsing or joining CSV files.
4. Run `scripts/validate_product_data.py` and resolve blocking schema errors.
5. Run `scripts/build_upload_tasks.py` to create a reviewable task CSV.
6. Validate media against [references/asset-requirements.md](references/asset-requirements.md). Mark unknown or conflicting cases `needs_manual_review`; do not guess.
7. Present the task summary and request explicit confirmation for the exact task IDs to publish.
8. After confirmation, follow [references/upload-ui-workflow.md](references/upload-ui-workflow.md). Prefer an authorized official API when documented; otherwise use DOM-based browser automation with the user's signed-in session.
9. Verify each submitted task in the material list. Record the remote material ID or other observable evidence.
10. Classify failures with [references/error-handling.md](references/error-handling.md) and write the final report using `assets/task-report-template.csv`.

## Commands

```powershell
python scripts/validate_product_data.py `
  --input "..\docs\天猫商品信息表_产品数据表_数据总表.csv" `
  --output "product-validation.json"

python scripts/build_upload_tasks.py `
  --products "..\docs\天猫商品信息表_产品数据表_数据总表.csv" `
  --rules "..\docs\天猫商品信息表_每月推品规则（合并）_Grid View.csv" `
  --month 7 `
  --asset-root "D:\search-materials" `
  --output "upload-tasks.csv"
```

Run commands from this skill directory. Treat generated titles and descriptions as drafts until the prohibited-words policy and source fields are confirmed.

## Safety Contract

- Never store passwords, cookies, access tokens, SMS codes, or QR-login data in this skill, CSV output, logs, or version control.
- Never publish, overwrite, delete, or replace remote material without explicit confirmation in the current task.
- Never call undocumented browser-internal endpoints directly. Capture them only for investigation and switch to API execution only after authorization and interface documentation are confirmed.
- Never infer a product ID, media license, product claim, age range, gender, or selling point from an ambiguous filename.
- Make reruns idempotent: check product ID, local media fingerprints, and existing remote material IDs before submitting.

## Completion Output

Report:

- input files and selected month;
- eligible, skipped, blocked, confirmed, submitted, successful, and failed counts;
- task IDs requiring manual review and their reasons;
- output report path;
- whether execution stopped before publishing or performed confirmed uploads.
