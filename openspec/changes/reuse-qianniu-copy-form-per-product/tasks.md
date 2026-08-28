## 1. Browser Product Session

- [x] 1.1 Add a product-scoped Qianniu copy session that validates all target empty-slot positions, opens one product-bound form, and exits it only after the product group finishes.
- [x] 1.2 Re-select the exact hash-verified seed image for every slot and support both initial “AI生成文案” and subsequent “重新生成” actions with stale-result rejection.
- [x] 1.3 Keep `generate_qianniu_copy_drafts` as a compatible grouped wrapper whose output order matches the original request order.

## 2. Durable Request Orchestration

- [x] 2.1 Group unfinished slots by product in first-appearance order and reuse one product session while preserving per-slot checkpoint and supersede checks.
- [x] 2.2 Preserve the existing initial attempt plus at most 3 retries, retryable reason-code set, exhausted-slot skip records, batch-fatal errors, and progress/response schema.

## 3. Contract and Verification

- [x] 3.1 Add browser and processor regression tests for one-form-per-product, slot isolation, stale-result rejection, restored completed products, retry, skip, fatal stop, and original response order.
- [x] 3.2 Update the canonical Skill and operations guide, regenerate the discovery entry, and run targeted plus bounded broader tests, syntax checks, Skill/OpenSpec validation, and `git diff --check`.
