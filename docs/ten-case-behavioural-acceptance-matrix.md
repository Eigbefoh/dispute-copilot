# Ten-Case Behavioural Acceptance Matrix

Status: **Proposed for approval**
Target corrected rulebook: `take-home-v4`
Historical rulebook preserved: `take-home-v3`

This matrix is derived from `cases.json`, every supplied evidence file, and the source Reason Codes PDF. Existing persisted recommendations were not treated as authoritative.

| Case | Policy | Expected requirement outcomes | Expected recommendation | Central limitation or gap |
|---|---|---|---|---|
| CB-2025-0001 | Visa 13.1 | R1 satisfied; R2 not applicable; R3 satisfied; R4 satisfied | `RECOMMEND_REPRESENT` | Delivery evidence does not prove personal use. |
| CB-2025-0002 | Visa 13.1 | R1 not established; R2 not applicable; R3 satisfied; R4 satisfied | `INSUFFICIENT_EVIDENCE_TO_REPRESENT` | No unique order-to-tracking linkage. |
| CB-2025-0003 | Mastercard 4837 | R1 satisfied; R2 satisfied; R3 not established; R4 not established | `RECOMMEND_REPRESENT` | Returning-device evidence is not a substitute for two prior undisputed transactions. |
| CB-2025-0004 | Mastercard 4837 | R1 not satisfied; R2 not satisfied; R3 not satisfied; R4 not established | `RECOMMEND_DO_NOT_REPRESENT` | Merchant confidence and internal risk scoring satisfy zero recognised routes. |
| CB-2025-0005 | Visa 12.6.1 | R1 satisfied; R2 satisfied; R3 satisfied | `RECOMMEND_REPRESENT` | Merchant conclusions are non-determinative; distinct IDs, baskets, timestamps and fulfilment are the proof. |
| CB-2025-0006 | Visa 13.3 | R1 satisfied; R2 not established; R3 not established | `INSUFFICIENT_EVIDENCE_TO_REPRESENT` | A front-view photograph cannot prove hidden condition or return-route behaviour. |
| CB-2025-0007 | Mastercard 4855 | R1 satisfied; R2 satisfied; R3 satisfied | `RECOMMEND_REPRESENT` | The transaction-specific record is on page 8; destination must be compared with shipping, not billing. |
| CB-2025-0008 | Visa 13.2 | R1 satisfied; R2 not established; R3 not established; R4 not established | `INSUFFICIENT_EVIDENCE_TO_REPRESENT` | General terms do not prove customer-specific notice, opt-in or non-cancellation. |
| CB-2025-0009 | Mastercard 4859 | R1 satisfied; R2 satisfied; R3 satisfied; R4 satisfied | `RECOMMEND_REPRESENT` | `NO-SHOW` is the established route; no cancellation timestamp is inferred. |
| CB-2025-0010 | Visa 10.5 | R1 not established | `RECOMMEND_DO_NOT_REPRESENT` | Authentication, delivery and prior orders do not prove issuer miscoding. |

## Release-blocking case: CB-2025-0004

The internal fraud report is relevant merchant-submitted evidence, but its confidence has no policy weight:

- AVS and CVV route: not satisfied because the frozen results are `N` and `N`.
- 3D Secure route: not satisfied because 3DS was not attempted.
- Prior-transactions route: not satisfied because the report states new account and first transaction.
- Signed-delivery route: not established because no signed delivery is supplied.
- Mastercard requires any two routes; the current record satisfies zero and can reach at most one through the remaining unknown.

The recommendation is therefore `RECOMMEND_DO_NOT_REPRESENT` / accept liability. A separate analyst action may request signed delivery or qualifying prior-transaction records if they exist.

## Central visual-evidence rule: CB-2025-0006

The system may admit:

- The image caption says a Carlton Armchair (Sage) was delivered on 18 April 2025.
- The visible image appears to show a sage-green armchair.
- The caption identifies the photograph as a front view only.

The system must not infer:

- The chair does not wobble.
- No hidden structural defect exists.
- The chair is defect-free.
- Every product characteristic matches the listing.
- The customer completed, refused or failed to use a return process.
- No visible damage means no damage exists.

## Buried evidence: CB-2025-0007

The consolidated manifest contains 10 pages. Page 8 contains `txn_7745MN`, collection from `LS1 4DT`, delivery to `LS9 8AA`, and signed receipt. The delivery destination must be compared with the frozen shipping postcode `LS9 8AA`; the billing postcode `LS1 4DT` is not a substitute.

## Recommendation versus next action

The recommendation reflects only the evidence currently admitted. Suggested analyst action is a separate deterministic output. For example:

- Recommendation: `RECOMMEND_DO_NOT_REPRESENT`.
- Suggested action: request a specific missing document that could satisfy a qualifying route.

Requesting evidence never changes the current recommendation by itself.

The complete facts, gaps, citations and source-line basis are recorded in [ten-case-behavioural-acceptance-matrix.json](./ten-case-behavioural-acceptance-matrix.json).
