# Dispute Copilot — Current System Status

Last updated: 30 July 2026

## Executive status

Dispute Copilot currently implements the complete pipeline from deterministic
case intake through offline deterministic requirement evaluation:

```text
Case intake
→ immutable CaseRun persisted in SQLite
→ deterministic policy selection
→ integrity-checked EvidenceBundle
→ immutable EvidenceDocuments
→ policy-requested fact resolution
→ deterministic fact admission
→ immutable FactResolutionResult persisted in SQLite
→ lineage and integrity validation
→ deterministic three-valued predicate interpretation
→ immutable RequirementEvaluationResult persisted atomically
→ deterministic versioned recommendation mapping
→ immutable RecommendationResult persisted atomically
→ READY_FOR_ANALYST_REVIEW
```

The production extraction branch is provider-pluggable. Exactly `nebius`,
`openai`, and `anthropic` are registered. Nebius evidence extraction preserves
its tested Chat Completions JSON-schema route. OpenAI uses native Responses,
and Anthropic uses native Messages with `output_config.format`. All three
share one canonical candidate schema, deterministic admission boundary, and
structured image-observation contract. OpenAI and Anthropic are implemented
and contract-tested; they are not represented as live validated until their
separate three-case qualification packs succeed.

Current qualification status for the amended three-provider contract:

- Nebius: `IMPLEMENTED_AND_CONTRACT_TESTED_NOT_LIVE_VALIDATED` for the new
  three-case qualification standard; earlier persisted Nebius results remain
  valid historical executions.
- OpenAI: `IMPLEMENTED_AND_CONTRACT_TESTED_NOT_LIVE_VALIDATED`.
- Anthropic: `IMPLEMENTED_AND_CONTRACT_TESTED_NOT_LIVE_VALIDATED`.

The first live CB-2025-0006 attempt completed and cached the Nebius image
description, then failed because Nebius rejected Responses JSON-schema
serialization. A second attempt reached Chat Completions and received provider
output, but that output failed the untrusted `ExtractionResponse` schema. No
fact result was admitted. A diagnostic-enabled retry showed that the model
mixed text and visual fields within individual provenance references. After
that contract was strengthened, the next live response passed the provider
schema but failed deterministic citation validation because a
`supporting_text` value was not copied from the canonical converted page text.
No fact result was admitted in those failed attempts. A subsequent live retry
passed all extraction and citation validation and reached
`READY_FOR_EVALUATION` with all ten Visa 13.3 facts accounted for.

## Completed milestones

### 1. Case intake

- Loads a case by `case_id` from `data/cases.json`.
- Validates the complete case with immutable Pydantic contracts.
- Registers evidence in declared order.
- Rejects missing files, directories masquerading as files, and unsupported
  media types.
- Records file size, MIME type, and SHA-256.
- Emits an immutable `CaseRun` with
  `READY_FOR_POLICY_SELECTION`.

### 2. SQLite CaseRun persistence

- Stores complete immutable case and evidence snapshots.
- Retrieves and reconstructs exact `CaseRun` objects.
- Rejects duplicate run IDs and overwrites.
- Preserves evidence hashes and metadata after process exit.

### 3. Versioned rulebook provisioning

- Preserves the source reason-code PDF outside SQLite.
- Stores source metadata and SHA-256.
- Validates the canonical transcript.
- Validates transcript line citations and exact quotations.
- Imports immutable executable policies into SQLite.
- Supports both `take-home-v1` and the reviewed `take-home-v2`.
- `take-home-v2` contains eight policies and a source-aware fact catalogue.

### 4. Deterministic policy selection

- Loads scheme and reason code only from the immutable CaseRun.
- Selects by exact:

```text
rulebook_version + scheme + reason_code
```

- Freezes the complete policy snapshot and hash.
- Persists selection separately from the CaseRun.
- Rejects missing policies and repeat selection for the same run.
- Emits `READY_FOR_EXTRACTION`.

### 5. Evidence-bundle loading

- Retrieves filenames from the immutable CaseRun.
- Rechecks every current file against the intake size and SHA-256.
- Rejects missing or modified evidence.
- Preserves declared evidence order.
- Does not discover extra files.

### 6. Document conversion

- Converts native-text PDFs while preserving one-based page numbers.
- Represents PNG evidence as one visual page.
- Records source SHA-256, conversion method, dimensions, converter version,
  and creation time.
- Persists immutable documents separately.
- Caches by `source_sha256 + converter_version`.
- Produces no policy conclusion.

### 7. Policy fact catalogue

- Every required fact has one registered definition.
- Definitions use controlled value types, source types, and cardinality.
- Source assertions are source-qualified.
- Visual observations remain distinct from text assertions.
- Case metadata bypasses probabilistic extraction.
- Comparisons, policy conclusions, and recommendations are excluded from
  extraction facts.

### 8. Fact-resolution admission contract

- Uses strict immutable Pydantic models.
- Enforces exact runtime types.
- Rejects duplicate candidate values.
- Requires source-specific provenance for every admitted value.
- Validates document ID, filename, page, extraction method, and supporting
  text.
- Derives requested facts only from the frozen policy snapshot.
- Only the deterministic factory can emit `READY_FOR_EVALUATION`.

### 9. Lean evidence extraction

- Sends only policy-requested evidence facts to the configured model.
- Keeps deterministic case metadata out of model input.
- Provides native PDF text and admitted image bytes.
- Requires strict structured output.
- Treats every provider response as untrusted.
- Converts candidates through the deterministic adapter.
- Combines metadata and evidence facts in frozen-policy order.
- Persists one immutable, content-hashed `FactResolutionResult` per selection.
- Returns the persisted result on repeat without invoking the provider.
- Does not evaluate requirements or recommend a dispute outcome.

### 10. Provider-pluggable live extraction

- Live inference is composed through an explicit provider registry.
- Registered providers are exactly `nebius`, `openai`, and `anthropic`.
- Nebius evidence extraction uses `OpenAIChatCompletions` with an explicit
  strict JSON schema.
- OpenAI uses the native Responses adapter.
- Anthropic uses the native Messages adapter, exact
  `output_config.format` JSON Schema, and base64 image blocks.
- New image conversions from every provider require separate
  `written_text_observations`, `visible_pixel_observations`, and
  `limitations`.
- The validated image structure is rendered deterministically under a new
  provider/converter cache identity. Historical converted documents and
  persisted results are not rewritten.
- Central frozen `ModelProviderSettings` validates configuration before
  evidence is read or encoded.
- Required settings:

```text
MODEL_PROVIDER
MODEL_NAME
MODEL_API_KEY
MODEL_BASE_URL
```

- Nebius has this registered base URL:

```text
https://api.tokenfactory.nebius.com/v1/
```

- Nebius, OpenAI, and Anthropic each have a registered default base URL.
- A supplied `MODEL_BASE_URL` must be a credential-free HTTP(S) URL.
- There is no default model.
- Provider and model selection belong exclusively to the user.
- There is no model substitution, provider fallback, or OpenAI endpoint
  fallback.
- Provider and endpoint errors are sanitised and never expose the credential.
- Models that reject structured output fail closed.
- Deprecated `NEBIUS_*` settings are isolated behind a compatibility bridge,
  emit a deprecation warning, and cannot be mixed with `MODEL_*`.
- The CLI depends only on generic settings and runtime composition.
- Every model-backed result records canonical non-secret execution identity:
  provider, exact model, adapter, prompt, schema, and material parameters.
- The SDK request-shape test confirms Nebius receives `schema`, never the
  incompatible `schema_` alias.
- Native SDK request-shape tests confirm OpenAI and Anthropic receive the
  canonical JSON Schema without an adapter-specific domain model.
- Different live models may return different valid wording or candidate
  values. The invariant is identical evidence scope, instructions, canonical
  result structure, and deterministic treatment.

### 11. Fail-closed provider-response diagnostics

- Pydantic response failures expose a bounded, sanitised list containing each
  failing field path, error type, and message.
- Raw values and evidence-derived response content are excluded from normal
  error output.
- Missing, duplicate, and unrequested fact candidates are reported before
  candidates leave the untrusted provider boundary.
- `--debug-provider-response` explicitly enables redacted raw-response
  capture.
- Debug artifacts use mode `0600` in the operating system temporary
  directory, are never stored in SQLite or the repository, and must be deleted
  after local review.
- Invalid responses are not partially admitted, repaired, retried without a
  schema, or routed to a different provider/model.

### 12. Discriminated provider provenance

- Provider references now use a strict `evidence_type` discriminator.
- `TEXT` requires `supporting_text` and structurally forbids
  `visual_observation`.
- `VISUAL` requires `visual_observation` and structurally forbids
  `supporting_text`.
- The prompt contains one valid example of each channel and requires separate
  references when writing and pixels both support a value.
- Writing printed inside a PNG is classified as TEXT evidence, while retaining
  the page's actual `VISION` conversion method.
- Provider references are explicitly converted into the existing deterministic
  text or visual provenance models.
- Parent `too_short` diagnostics caused by rejected child references are
  suppressed; genuine empty-reference failures remain visible.
- Prompt identity is now `lean-evidence-extraction/0.4.0`.
- Provider schema identity is now `extraction-response/0.3.0`.

### 13. Exact canonical-text citation boundary

- Provider input labels converted page content as `canonical_text`.
- The prompt requires every TEXT `supporting_text` value to be copied
  exclusively and verbatim from that supplied field.
- The model is forbidden from independently transcribing image pixels,
  paraphrasing, correcting, or synthesising a quotation.
- Citation failures report the exact candidate/value/reference path,
  document filename and ID, page number, rejected supporting text, and
  whether canonical page text is empty.
- Diagnostic failures do not print the complete canonical page text.
- Both untrusted adaptation and final admission use one versioned
  deterministic normaliser: Unicode NFKC plus collapsed whitespace.
- Matching remains case-sensitive and punctuation-sensitive. There is no
  fuzzy matching, partial admission, or citation repair.
- The cached CB-2025-0006 delivery-photo conversion was inspected and contains
  all three visible captions in its canonical page text.

### 14. Deterministic requirement evaluation

- Adds a versioned declarative predicate manifest for every requirement in all
  eight take-home-v2 policies.
- Validates exact policy requirement order, complete fact coverage, catalogue
  membership, operand types, and allow-listed operators before evaluation.
- Supports `all_of`, `any_of`, fact presence/equality/membership, typed date
  comparisons, bounded date distance, and numeric boundaries.
- Uses explicit `TRUE`, `FALSE`, and `UNKNOWN` logic.
- Maps results to `SATISFIED`, `NOT_SATISFIED`, and `NOT_ESTABLISHED`.
- Treats `NOT_FOUND`, `AMBIGUOUS`, and `CONFLICTING` as `UNKNOWN`, never as
  proof of a negative proposition.
- Preserves exact admitted value indexes and validated provenance in fact
  bindings.
- Aggregates requirement outcomes with fixed deterministic precedence.
- Persists `RequirementEvaluationResult` and its
  `READY_FOR_RECOMMENDATION` workflow event in one SQLite transaction.
- Returns an existing result for an identical idempotent replay and rejects
  conflicting history.
- Performs no model, network, OCR, PDF, image, or evidence-file access.
- Produces no recommendation or policy decision.

### 15. Deterministic recommendation mapping

- Loads only the authoritative persisted `RequirementEvaluationResult`, frozen
  selection metadata, workflow progression, and explicit mapping manifest.
- Revalidates evaluation content hash, run/selection/resolution/policy lineage,
  selected-policy requirement order, and effective
  `READY_FOR_RECOMMENDATION` state.
- Recomputes the aggregate from requirement outcomes before mapping.
- Resolves an explicitly supplied mapping key and version with no default,
  fallback, or substitution.
- Uses the frozen manifest
  `default-representment-eligibility-v1/1.0.0`, whose canonical integrity hash
  is `a591855ccccef1c8388b4f4576da014163b89d7199780d84c9f50e2957caeea4`.
- Maps all three supported aggregates through exactly one ordered rule.
- Copies causal requirement IDs, order, statuses, and evaluation basis codes
  without loading or interpreting fact values or provenance.
- Persists the immutable `RecommendationResult` and
  `READY_FOR_ANALYST_REVIEW` workflow event atomically.
- Identical replay returns the existing record and creates no duplicate event.
- Performs no model, network, OCR, PDF, image, fact-repository, or evidence
  access.
- Produces no representment prose and performs no external action.

## Current CB-2025-0006 state

### Existing take-home-v1 lane

```text
Run:
bef8c7b2-de60-4e94-90de-33a8e714c60b

Selection:
b9a1c5fd-2619-4468-b457-ccce27b57ad1

Policy:
visa-13.3-take-home-v1

Status:
READY_FOR_EXTRACTION
```

This historical lane remains unchanged.

### Active take-home-v2 lane

```text
Run:
cf8687c3-a58d-4fd4-9c07-9d4c7df5b3e9

Selection:
1cb6b917-62c5-41b1-b9ca-0c0659e5299a

Policy:
visa-13.3-take-home-v2

Effective status:
READY_FOR_ANALYST_REVIEW
```

Evidence bundle, in frozen order:

1. `CB-2025-0006_product_listing.pdf`
2. `CB-2025-0006_delivery_photo.png`
3. `CB-2025-0006_returns_policy.pdf`

### Live extraction state

- The delivery-photo source description was created by
  `Qwen/Qwen2.5-VL-72B-Instruct` and cached at
  `2026-07-29T17:11:00.752481+00:00`.
- The subsequent Responses structured-output request returned HTTP 400.
- After the Chat Completions fix, a second live response failed local
  `ExtractionResponse` validation.
- The diagnostic retry identified six primary channel-mixing errors: TEXT
  references included visual observations and VISUAL references included
  supporting text.
- The subsequent live response passed the discriminated provider schema but
  cited supporting text that did not occur in the canonical converted page
  text.
- The cached delivery-photo canonical text contains:
  `HavenHome — Delivery Photo`,
  `Carlton Armchair (Sage) — delivered 18 Apr 2025`, and
  `Photo taken by driver at delivery (front view only)`.
- The final retry admitted all ten requested facts and emitted
  `READY_FOR_EVALUATION`.
- The successful result was rerun under the persistence boundary and stored as
  resolution `6a58ecc4-2782-4136-a7af-e21571ffdb50`.
- Offline evaluation
  `bed922b8-2aa6-48c8-bb09-7e174465683d` produced one satisfied and two
  not-established Visa 13.3 requirements.
- Offline recommendation
  `c7ade87f-6845-4be8-be26-ce914b7e14dc` produced
  `INSUFFICIENT_EVIDENCE_TO_REPRESENT`.
- The causal requirements are `V13.3-R2` and `V13.3-R3`.
- The workflow now has immutable progression events through
  `READY_FOR_ANALYST_REVIEW`.
- Replay returned the same recommendation ID and SQLite contains exactly one
  recommendation for the evaluation.

## Visa 13.3 requested facts

The frozen `take-home-v2` policy requests ten facts:

1. `merchant_stated_product_description`
2. `image_observed_item_colour`
3. `image_observed_item_type`
4. `image_observed_visible_damage`
5. `image_observed_serial_number`
6. `merchant_stated_return_method`
7. `merchant_stated_return_window_days`
8. `merchant_recorded_return_request_date`
9. `merchant_recorded_post_complaint_usage_date`
10. `merchant_recorded_merchandise_retention_status`

All ten are evidence facts. The current Visa 13.3 policy requests no
`CASE_METADATA` facts.

## Verification status

Latest full test result:

```text
345 passed
1 skipped
```

The skipped test is the explicitly gated live provider integration test. It runs
only when all of the following are present:

```text
RUN_MODEL_PROVIDER_INTEGRATION=1
MODEL_PROVIDER
MODEL_API_KEY
MODEL_NAME
```

Additional verification:

- Python compilation succeeds.
- Installed dependencies are internally consistent.
- Mocked CB-2025-0006 extraction reaches `READY_FOR_EVALUATION`.
- Original CaseRun and PolicySelection snapshots remain unchanged.
- Missing model-provider configuration stops before evidence resolution.
- Unsupported providers stop before transport creation.
- The CLI has no provider-specific imports or construction.
- None of the three registered provider routes substitutes a provider or
  model.
- Native SDKs are exercised through local mock transports to verify the exact
  Nebius Chat Completions, OpenAI Responses, and Anthropic Messages
  structured-output request bodies.
- Shared provider tests prove one canonical candidate type and one
  deterministic rejection boundary without promising identical live-model
  wording.
- CB-2025-0006 image-contract tests preserve written claims, pixel
  observations, and view limitations as separate fields.
- Diagnostic tests verify field-level error summaries, exact requested-fact
  coverage, credential redaction, restricted temporary-file permissions, and
  the absence of raw artifacts in normal mode.
- Provenance tests verify the discriminated JSON schema, mutual exclusion,
  suppression of derivative diagnostics, and TEXT citations from writing
  inside visual files.
- Citation tests verify candidate-path diagnostics, canonical caption
  preservation, Unicode/whitespace normalisation, and rejection of
  case-changed, punctuation-changed, and paraphrased text.
- Predicate tests cover three-valued truth tables, inclusive typed
  comparisons, unknown/ambiguous/conflicting facts, invalid operators, and
  complete executable coverage for all eight policies.
- Repository-backed CB-2025-0006 tests prove exact ordered outcomes,
  persisted fact/evaluation reconstruction, idempotency, atomic failure,
  unchanged CaseRun and PolicySelection bytes, and zero evidence-file reads
  during evaluation.
- Recommendation tests cover all three aggregate mappings, explicit
  NOT_SATISFIED precedence, manifest integrity, causal requirement ordering,
  stable semantic hashes, atomic failure, idempotent replay, and zero earlier
  boundary access.
- No API credential or secret-derived value is stored in this document.

## Current workflow boundary

The deterministic and mocked path has proven:

```text
CB-2025-0006
→ take-home-v2 Visa 13.3 selection
→ complete three-file evidence bundle
→ evidence conversion
→ structured candidate facts
→ deterministic validation
→ persisted FactResolutionResult
→ ordered Visa 13.3 requirement evaluation
→ V13.3-R1 SATISFIED
→ V13.3-R2 NOT_ESTABLISHED
→ V13.3-R3 NOT_ESTABLISHED
→ ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
→ READY_FOR_RECOMMENDATION
→ INSUFFICIENT_EVIDENCE_TO_REPRESENT
→ causal requirements V13.3-R2 and V13.3-R3
→ READY_FOR_ANALYST_REVIEW
```

The real persisted CB-2025-0006 vertical slice is complete through analyst
review readiness:

```text
CaseRun cf8687c3-a58d-4fd4-9c07-9d4c7df5b3e9
→ Selection 1cb6b917-62c5-41b1-b9ca-0c0659e5299a
→ Resolution 6a58ecc4-2782-4136-a7af-e21571ffdb50
→ Evaluation bed922b8-2aa6-48c8-bb09-7e174465683d
→ Recommendation c7ade87f-6845-4be8-be26-ce914b7e14dc
→ READY_FOR_ANALYST_REVIEW
```

## Resume point

Before a real run:

1. The user selects the exact registered provider and model.
2. `MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_API_KEY`, and any required
   `MODEL_BASE_URL` are supplied through a secure, Git-excluded local
   environment.
3. The user explicitly authorises the live run.

Replay the recommendation offline:

```bash
python -m dispute_copilot.recommendation.recommend \
  --evaluation-id bed922b8-2aa6-48c8-bb09-7e174465683d \
  --mapping-key default-representment-eligibility-v1 \
  --mapping-version 1.0.0
```

This returns the existing immutable recommendation and performs zero model or
evidence access.

## Intentionally not implemented

- Automatic model selection, routing, or benchmarking
- Provider or model fallback
- Cross-provider retry or substitution
- Provider-specific fact, policy, evaluation, or recommendation logic
- Unstructured JSON fallback or response repair
- Partial fact admission
- Automatic live-provider qualification
- Scheme submission or merchant communication

## Recommended next milestone

After explicit billable-run approval and secure credentials are available,
run the isolated CB-2025-0004, CB-2025-0006, and CB-2025-0007 qualification
pack separately for each provider. Until then, the honest provider status is
`IMPLEMENTED_AND_CONTRACT_TESTED_NOT_LIVE_VALIDATED`.
