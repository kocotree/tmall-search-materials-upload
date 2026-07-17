# Upload UI Workflow

## Technology choice

1. Use an authorized official API only when its documentation, credentials, scopes, rate limits, idempotency behavior, and status-query endpoint are available.
2. Otherwise use Playwright or the in-app browser against the user's existing signed-in Tmall session.
3. Use semantic DOM roles, labels, and nearby text. Avoid fixed screen coordinates.
4. Keep visual computer-use actions as a fallback for controls that have no stable DOM representation.
5. Do not use Firecrawl for authenticated upload and publishing.

## Current navigation

Navigate to `商品 -> 素材中心 -> 商品素材管理 -> 搜推素材`, search by exact product ID, and open the empty material slot. Choose `发图文` or `发视频` according to the confirmed task plan.

## Publish sequence

1. Confirm the store identity and signed-in account.
2. Search for the exact product ID and verify the displayed product name.
3. Count existing and empty slots; stop on disagreement with the task.
4. Upload validated files.
5. Fill the reviewed title and description.
6. Pause at the final publish action and obtain explicit confirmation for the exact task ID unless already supplied in the current task.
7. Publish once.
8. Wait for a visible success or moderation state.
9. Return to the list and verify the slot/material count.
10. Record the remote material ID, visible state, timestamp, and screenshot or text evidence.

## Selector maintenance

Do not freeze CSS selectors from screenshots. During implementation, record stable roles/labels and maintain a small page-object layer. If the page layout or wording changes, stop publication and mark tasks `needs_manual_review` until selectors are revalidated.
