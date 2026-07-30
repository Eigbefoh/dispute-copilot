# Evidence Region Capability Audit

## Scope

This audit records the display capability available for the six supporting
source records in the persisted `CB-2025-0006` analyst review package:

- Review package: `ff9d317b-ca4e-4047-b081-4dbd3ea204f5`
- Package hash:
  `9ab60106d732ca7b370cb218d6d39c0a2cc8b2962930d86a74e783cb441f472d`
- Fact resolution: `6a58ecc4-2782-4136-a7af-e21571ffdb50`

The audit distinguishes document/page provenance from grounded region
coordinates. A source and page reference does not imply that a defensible
bounding box exists.

## Persisted capability

| Fact | Source | Persisted method | Region capability |
|---|---|---:|---|
| `merchant_stated_product_description` | Product listing PDF, page 1 | `NATIVE_TEXT` | `PAGE_ONLY` |
| `image_observed_item_colour` | Delivery photo, page 1 | `VISION` | `WHOLE_IMAGE_CONTEXT` |
| `image_observed_item_type` | Delivery photo, page 1 | `VISION` | `WHOLE_IMAGE_CONTEXT` |
| `merchant_stated_return_method` value 1 | Returns policy PDF, page 1 | `NATIVE_TEXT` | `PAGE_ONLY` |
| `merchant_stated_return_method` value 2 | Returns policy PDF, page 1 | `NATIVE_TEXT` | `PAGE_ONLY` |
| `merchant_stated_return_window_days` | Returns policy PDF, page 1 | `NATIVE_TEXT` | `PAGE_ONLY` |

The converted delivery-photo page preserves whole-image width and height.
Neither `VisualObservationProvenance` record contains an object region. The
converted PDF pages and `EvidenceTextProvenance` records preserve text and page
identity but no word, line or block coordinates.

## Version 1 display rule

- PDFs are rendered at their exact cited page with `PAGE_ONLY`.
- The delivery photo is rendered in full with `WHOLE_IMAGE_CONTEXT`.
- No yellow rectangle is drawn for any current `CB-2025-0006` source.
- Evidence gaps use `NOT_APPLICABLE` and never receive coordinates.
- Native-PDF coordinate derivation is not enabled in renderer version 1.
  Introducing it requires a separately versioned, exact-match coordinate
  method and new tests.
- OCR and vision are never rerun to improve display precision.

For the chair photo the analyst must see this limitation: the pixels support a
model observation only; they do not prove exact product identity, defect-free
condition or policy satisfaction.
