# Asset Requirements

## Images confirmed from the current UI

- Upload 3 to 9 images for one image-text material.
- Use either 3:4 or 1:1 within one material; do not mix ratios.
- Recommend at least 1440 x 1920 for 3:4.
- Recommend at least 1440 x 1440 for 1:1.
- Crop or preprocess mismatched images before publishing.

## Text confirmed from the current UI

- Recommend 8 to 10 Chinese characters for the title; enforce the UI maximum of 20 characters.
- Keep the description between 10 and 1000 characters.
- Generate text from documented product attributes only.

## Video policy pending confirmation

Do not automatically publish a video until the accepted container, codec, duration, aspect ratio, resolution, file-size limit, cover rule, watermark rule, and moderation-state behavior are recorded.

## Local validation

- Reject zero-byte, unreadable, or unsupported files.
- Calculate a SHA-256 fingerprint for duplicate and rerun detection.
- Preserve originals; create processed derivatives in a separate output directory.
- Record the source and usage-rights status for every media file.
- Mark any unknown license, ratio, or product association `needs_manual_review`.
