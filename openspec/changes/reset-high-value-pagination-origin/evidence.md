# Live Acceptance Evidence

## Verified full scan

- Session: `20260730_052506`
- Attempt: `854b6e93974b40cc8cf8cd8b69913d0d`
- Starting browser state: reused CDP tab selected on page 26 of 26
- Verified origin before first checkpoint: page 1, 10 rows
- Verified traversal: 25 transitions covering pages 2 through 26 exactly once
- Verified terminal: page 26 of 26, 5 rows, next disabled, stable terminal proof
- Reconciled result: 255 CSV rows, 255 unique non-empty product IDs, checkpoint page 26 and row count 255
- Worker result: completed with origin page 1, observed page 26, terminal page 26, and `PAGINATION_TERMINAL_VERIFIED`
- Completeness result: 255 products; pagination origin 1, current 26, terminal 26, terminal proof true
- Safety boundary: no `03-upload`, `04-approval`, or `05-publish` files or directories were created

## Evidence paths and SHA-256

- `runs/20260730_052506/collected/promotion/current/selector-profile.json`
  - `6b105c00ad31c559a1cc0557500ee0de16fb422f476596a9d1615b99d91fe371`
- `runs/20260730_052506/collected/promotion/current/store-page-evidence.json`
  - `1c1b4129096a194a1b9c43fe0ae81f6050c7c3f81482edc6ebabce26092e3cda`
- `runs/20260730_052506/collected/promotion/current/promotion-material-status.csv`
  - `0463ef3e254c6555329680c5f3d5fb0cad03caa13908214580af93b88bbab920`
- `runs/20260730_052506/collected/promotion/current/promotion-material-status.checkpoint.json`
  - `5d4bae202bb752643cb378e788283fa333992eb17146b43153d733e15c7747ea`
- `runs/20260730_052506/collected/promotion/current/pagination-evidence.json`
  - `ad2243a54ca120e8ae53514edeff614295f37577a49d1589368ecfb5a45c15be`
- `runs/20260730_052506/collected/promotion/current/publication.json`
  - `ff2a49dc89d3bb8ae355346db486a2e6d7bd623a4323abeea95b8e698da54dfe`
- `runs/20260730_052506/02-completeness/completeness-matrix.json`
  - `75f5d00f7d33e9a592fbedd84632bf8265e88bbcec48e458cc6139c1c6e19794`

The checkpoint's CSV and pagination-evidence hashes match the files above. The
publication manifest binds the same attempt, session, revision, input hash,
selector hash, row count, CSV hash, checkpoint hash, and pagination-evidence
hash.

## Acceptance defect discovered and isolated

The first live acceptance attempt, session `20260730_051941`, exposed a SPA
race where the page indicator reached page 1 before the table rows changed
from the stale final-page contents. The origin stabilizer now requires the
ordered product-ID hash to change when resetting from a later page. That
attempt is quarantined and is not accepted as downstream evidence.

## Interrupted-run resume

- Session: `20260730_053439`
- Attempt: `dd02832e93b349e49c2f801c62bc6ef5`
- Interrupted checkpoint: page 6, 60 rows
- Browser state at interruption: page 7, unrelated to both the verified
  page-1 origin and the page-6 checkpoint boundary
- Recovery status after interruption: `recoverable`, with
  `resume_exact_session`
- Resume behavior: verified page 1 again, replayed transitions through page 6
  without re-persisting their rows, then collected from page 7
- Final result: page 26, 255 rows, 255 unique non-empty product IDs, terminal
  proof true, and `PAGINATION_TERMINAL_VERIFIED`
- Duplicate and checkpoint reconciliation: zero duplicate product IDs; final
  checkpoint row count 255 and CSV SHA-256 match; the same attempt ID was
  preserved across interruption and resume
- Safety boundary: no `03-upload`, `04-approval`, or `05-publish` files or
  directories were created

### Resume evidence paths and SHA-256

- `runs/20260730_053439/collected/promotion/current/promotion-material-status.csv`
  - `c1062aa57b3181b8097b5994495c0fd5bfafeae2a2c3521edc0b513b24efc0af`
- `runs/20260730_053439/collected/promotion/current/promotion-material-status.checkpoint.json`
  - `9ac682c3690e2b519cb5922e0cf21e46bad92dd06ceac45b699a2c48e93bb655`
- `runs/20260730_053439/collected/promotion/current/pagination-evidence.json`
  - `9cadfadd114375a333f621da800d488229cf0def2b2fc587e86a1041dca0bde1`
- `runs/20260730_053439/collected/promotion/current/publication.json`
  - `642008f87fd53b24e22ada1c699ea97c814aa11d9e6a53a7ac7a2e63927c9ea7`
- `runs/20260730_053439/02-completeness/completeness-matrix.json`
  - `eebe5d90e731c7d811e26acc131ec44cd8277f20d3714a7a62414cfaef5fed19`
