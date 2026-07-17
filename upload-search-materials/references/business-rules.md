# Business Rules

## Confirmed rules

- Select products from the monthly category and product-grade rules.
- Exclude products whose product grade is `清仓`.
- Do not maintain good-experience, member-day, or points-only materials when those labels are reliably identifiable.
- Fill every required search-recommendation slot.
- For a 3-slot product with both media types, use 1 video and 2 image-text materials.
- For a 9-slot product with both media types, use up to 3 videos and 6 image-text materials.
- If no video exists, prioritize image-text material.
- Draft titles as `KK树 + 年龄或性别 + 品类词 + 卖点词`.
- Use model-library, authorized Xiaohongshu/NAS, Feishu, and Guanghe short-video sources only when usage rights are confirmed.

## Unresolved rules

Mark a task `needs_manual_review` until the relevant rule is supplied:

- Which product field identifies `UVNO`, good-experience, member-day, points-only, inactive, or special-supply products.
- Which table wins if monthly category and grade tables conflict.
- How to resolve duplicate product IDs with different names or grades.
- Whether new products missing a product ID should be skipped or queued until listing completes.
- Whether B-grade products ever qualify.
- How to derive age, gender, category term, and selling points without inference.
- Prohibited advertising terms and content-review policy.
- Whether existing material may be reused across products.

## Selection decision

1. Require a non-empty unique product ID for upload.
2. Exclude `清仓`.
3. Match the selected month and company category to the merged monthly rules table.
4. Match the normalized product grade to the permitted grades.
5. Apply only exclusion labels that map to documented fields.
6. Send duplicate IDs, conflicting records, and missing source fields to manual review.
