# Lean Evidence Extraction — Implementation Plan

Status: **IMPLEMENTED**

Provider configuration note: the original provider-specific configuration in
this plan has been superseded by the provider-pluggable runtime. The user must
supply `MODEL_PROVIDER`, `MODEL_NAME`, and `MODEL_API_KEY`, plus
`MODEL_BASE_URL` when the registered provider requires it. There is no default
or fallback provider or model.

## 1. Milestone outcome

Close the functional gap between converted evidence and the existing
deterministic fact-admission boundary:

```text
CB-2025-0006 CaseRun
→ take-home-v2 Visa 13.3 PolicySelection
→ integrity-checked EvidenceBundle
→ immutable EvidenceDocuments
→ deterministic metadata resolution
→ one multimodal extraction model
→ untrusted extraction candidates
→ deterministic candidate adaptation and admission
→ immutable FactResolutionResult
→ READY_FOR_EVALUATION
```

This milestone extracts evidence facts. It does not determine whether a
requirement is satisfied and does not recommend an outcome.

## 2. Verified starting state and prerequisites

The current regression baseline is **150 passing tests**.

The current database contains a CB-2025-0006 selection for
`take-home-v1`, not `take-home-v2`. Because policy selections and case
runs are immutable and one selection is allowed per run, implementation
will:

1. Preserve the existing v1 run and selection unchanged.
2. Create a fresh CaseRun for CB-2025-0006 using the existing intake
   service.
3. Select Visa 13.3 from `take-home-v2` for that new run.
4. Use the new selection ID for the extraction proof.

The live database does not yet contain the `evidence_documents` table.
The extraction entry point will therefore use the existing conversion
service, which creates the table lazily and caches each conversion by
`source_sha256 + converter_version`.

The complete immutable evidence bundle contains, in declared order:

1. `CB-2025-0006_product_listing.pdf`
2. `CB-2025-0006_delivery_photo.png`
3. `CB-2025-0006_returns_policy.pdf`

All three files will be loaded and converted. The product listing cannot
be omitted because it is the evidence source for the merchant-stated
product description.

## 3. Exact Visa 13.3 extraction target

The requested fact order will always be derived from the frozen policy
snapshot, never from a prompt constant or the current policy table.

### CASE_METADATA

None for the current `take-home-v2` Visa 13.3 policy.

The generic metadata branch will still run through the existing router,
but its input is empty for CB-2025-0006. No substitute metadata facts
will be invented.

### EVIDENCE_TEXT_ASSERTION

| Order | Fact | Type | Cardinality |
|---:|---|---|---|
| 1 | `merchant_stated_product_description` | STRING | SINGLE |
| 6 | `merchant_stated_return_method` | STRING | MULTIPLE |
| 7 | `merchant_stated_return_window_days` | INTEGER | SINGLE |
| 8 | `merchant_recorded_return_request_date` | DATE | MULTIPLE |
| 9 | `merchant_recorded_post_complaint_usage_date` | DATE | MULTIPLE |
| 10 | `merchant_recorded_merchandise_retention_status` | STRING | SINGLE |

### EVIDENCE_VISUAL_OBSERVATION

| Order | Fact | Type | Cardinality |
|---:|---|---|---|
| 2 | `image_observed_item_colour` | STRING | MULTIPLE |
| 3 | `image_observed_item_type` | STRING | MULTIPLE |
| 4 | `image_observed_visible_damage` | STRING | MULTIPLE |
| 5 | `image_observed_serial_number` | STRING | MULTIPLE |

The final tuple passed to the admission factory will restore the exact
1–10 policy order above.

## 4. Proposed code changes

### 4.1 Untrusted provider response models

Add:

`src/dispute_copilot/extraction/evidence_extraction_models.py`

Define strict Pydantic models with `extra="forbid"`:

```text
ExtractionResponse
└── candidates[]
    ├── fact_name
    ├── status: FOUND | NOT_FOUND | AMBIGUOUS | CONFLICTING
    ├── confidence
    └── values[]
        ├── raw_value
        └── references[]
            ├── source_type
            ├── document_id
            ├── file_name
            ├── page_number
            ├── extraction_method
            ├── supporting_text
            └── observation
```

The provider schema will not contain workflow status, policy
satisfaction, requirement results, recommendations, or decisions.

`raw_value` remains untrusted. Exact catalogue typing happens locally in
the adapter, not through permissive Pydantic coercion.

### 4.2 One concrete multimodal extractor

Add:

`src/dispute_copilot/extraction/evidence_extractor.py`

Implement one small concrete client:

```python
extract_evidence_candidates(
    *,
    requested_facts: tuple[FactDefinition, ...],
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
) -> tuple[ExtractionCandidate, ...]
```

The bundle is included because `EvidenceDocument` intentionally does not
retain a runtime file path, while the model needs the admitted image
bytes. The function will verify bundle/document correspondence before
constructing model input.

The extractor will:

- receive only facts routed to evidence extraction;
- include exact fact name, definition, value type, source type, and
  cardinality;
- provide page-tagged native PDF text from `EvidenceDocument`;
- provide the admitted delivery-photo bytes as an image input;
- identify sources using the existing document ID, file name, and page;
- require strict structured output matching `ExtractionResponse`;
- return exactly one candidate per requested evidence fact.

The implementation uses the `openai` Python SDK as a Responses API-family
transport client behind an explicit provider registry. The exact provider,
model, credential, and any required endpoint are supplied through generic
`MODEL_*` configuration. Structured output constrains the response to the
untrusted Pydantic schema. No implicit OpenAI endpoint, default model, provider
fallback, or model substitution is permitted.

`nebius` and `openai_compatible` are registered. Unsupported providers fail
before transport creation. The transport client remains injectable for unit
tests.

### 4.3 Reuse document conversion with the same model

The PDFs already use native text extraction. The existing PNG converter
requires a `VisionProvider` implementation to create its immutable visual
page.

Add a minimal adapter around the same configured model client that
implements the existing `VisionProvider` protocol. It will describe only
visible image content and will not make policy claims.

This creates two bounded calls when the PNG is not cached:

1. a source-preserving image description for `EvidenceDocument`;
2. one structured fact-extraction request over all documents and the
   admitted image.

Both use the same model integration. This avoids adding separate OCR and
vision architectures while preserving the already-approved conversion
contract and cache.

### 4.4 Deterministic candidate adapter

Add:

`src/dispute_copilot/extraction/evidence_candidate_adapter.py`

Implement:

```python
to_resolved_facts(
    *,
    provider_candidates: tuple[ExtractionCandidate, ...],
    requested_facts: tuple[FactDefinition, ...],
    evidence_documents: tuple[EvidenceDocument, ...],
) -> tuple[ResolvedFact, ...]
```

The adapter will:

- reject missing, duplicate, and unrequested facts;
- require exactly one candidate record for each requested fact;
- parse each raw value using the catalogue’s authoritative type;
- reject coercive or lossy forms;
- reject duplicate value entries; one value may instead contain multiple
  distinct supporting references under one `ResolvedValue`;
- preserve `NOT_FOUND`, `AMBIGUOUS`, and `CONFLICTING`;
- map text references to `EvidenceTextProvenance`;
- map visual references to `VisualObservationProvenance`;
- reject unknown document IDs, mismatched file names, invalid pages, and
  source-channel mismatches;
- require text support to exist on the cited converted page;
- never invent a value or provenance record.

The exact type rules will match the existing admission factory:

- STRING and ADDRESS: exact string;
- INTEGER: strict integer, excluding booleans and decimal-looking text;
- DECIMAL: lossless `Decimal`;
- BOOLEAN: explicit boolean only;
- DATE: ISO date with no time component;
- DATETIME: ISO datetime with timezone.

### 4.5 Thin resolution orchestration

Add:

`src/dispute_copilot/extraction/fact_resolution_service.py`

Provide a testable core:

```python
resolve_case_facts(
    *,
    case_run: CaseRun,
    selection: PolicySelection,
    catalogue: FactCatalogue,
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
    extractor: EvidenceExtractor,
) -> FactResolutionResult
```

Execution order:

1. Derive requested facts from `selection.policy_snapshot`.
2. Load matching definitions from the exact-version catalogue.
3. Route definitions with the existing fact router.
4. Resolve `CASE_METADATA` definitions with the existing deterministic
   resolver.
5. Send only evidence definitions to the model.
6. Adapt the untrusted provider response locally.
7. Merge metadata and evidence facts in frozen-policy order.
8. Call the existing `create_fact_resolution_result(...)`.
9. Return the immutable admitted result.

Add a small repository-backed wrapper that accepts only `selection_id`
and `database_path`. It will load the selection and run, recheck evidence
hashes through `load_evidence_bundle`, load the matching catalogue,
ensure converted documents exist through the current cache, and call the
core service.

Failure is closed: model error, refusal, malformed output, incomplete
coverage, invalid type, invalid provenance, or admission failure returns
no `FactResolutionResult` and therefore no `READY_FOR_EVALUATION`.

### 4.6 One command for the proof

Add a narrow module entry point:

```text
python -m dispute_copilot.extraction.resolve_facts <selection-id>
```

Optional flags will be limited to database path, model configuration, and
verbose output. This does not add an API or UI and does not persist the
result.

Successful output will report:

- run, selection, policy, and rulebook identity;
- all requested facts in policy order;
- route for each fact;
- status and admitted values;
- document/page provenance for each admitted value;
- final status `READY_FOR_EVALUATION`;
- an explicit statement that no evaluation or recommendation was
  produced.

## 5. Prompt constraints

The model instructions will state:

- extract evidence facts; do not decide the dispute;
- answer only the supplied requested facts;
- distinguish `FOUND`, `NOT_FOUND`, `AMBIGUOUS`, and `CONFLICTING`;
- cite every value to a supplied document and page;
- treat text citations as source assertions, not established truth;
- treat visual descriptions as observations, not quotations;
- do not compare the image with the product listing;
- do not determine defect, policy satisfaction, customer truthfulness,
  or a merchant action;
- do not add facts noticed incidentally.

The model may propose `NOT_FOUND` for facts absent from the supplied
evidence. The implementation will not hardcode expected evidence values
or force a desired status.

## 6. Error model

Add focused typed exceptions for:

- model invocation or refusal;
- invalid structured response;
- incomplete or duplicate candidate coverage;
- candidate value parsing;
- candidate provenance adaptation.

These errors describe why admission stopped. This milestone will not
persist provider attempts, errors, partial candidates, or results.

## 7. Test plan

### Unit tests with an injected fake model

- provider receives only frozen-policy evidence facts;
- metadata facts are never sent to the model;
- all three declared documents are supplied in original order;
- the raw delivery photo is supplied from the integrity-checked bundle;
- one candidate is required per requested evidence fact;
- unrequested, missing, and duplicate facts are rejected;
- incorrect and coercive value types fail closed;
- duplicate value entries are rejected, while one value with multiple
  distinct provenance records is accepted;
- invalid document ID, file name, page, or source channel is rejected;
- text references produce `EvidenceTextProvenance`;
- visual references produce `VisualObservationProvenance`;
- `NOT_FOUND`, `AMBIGUOUS`, and `CONFLICTING` survive adaptation;
- metadata and evidence candidates are restored to policy order;
- the prompt/schema contains no requirement result, decision, or
  recommendation field;
- model failure produces no result;
- the original CaseRun, PolicySelection, EvidenceBundle, and
  EvidenceDocuments remain unchanged.

### Repository-backed integration test with a fake model

Create a fresh CB-2025-0006 run, select `take-home-v2`, convert/cache the
complete bundle, extract mocked candidates, and prove the existing
admission factory returns:

```text
FactResolutionResult.status == READY_FOR_EVALUATION
```

### Optional real-model integration test

Add a separately marked test that is skipped unless explicitly enabled with a
configured registered provider, credential, and user-selected model. It will
use the real CB-2025-0006 evidence and assert contract properties rather than
hardcoding evidence-derived answers:

- exact requested-fact coverage;
- valid types;
- valid provenance;
- successful deterministic admission;
- no evaluation or recommendation output.

### Regression gate

Run:

```text
.venv/bin/python -m pytest -q
```

The existing 150 tests and all new non-network tests must pass. The real
model proof will then be run explicitly and reported separately.

## 8. Files expected to change

New:

- `src/dispute_copilot/extraction/evidence_extraction_models.py`
- `src/dispute_copilot/extraction/evidence_extractor.py`
- `src/dispute_copilot/extraction/evidence_candidate_adapter.py`
- `src/dispute_copilot/extraction/fact_resolution_service.py`
- `src/dispute_copilot/extraction/resolve_facts.py`
- focused unit/integration test modules

Small updates:

- `pyproject.toml` for the model client dependency and integration-test
  marker;
- `.env.example` only for non-secret configuration names;
- possibly `src/dispute_copilot/config.py` if central configuration is
  needed;
- existing extraction exports where useful.

No existing immutable domain snapshot or policy contract will be
redesigned unless an implementation test demonstrates a genuine blocker.

## 9. Explicitly out of scope

- separate OCR and vision provider architectures;
- extraction-attempt persistence;
- replay or model-comparison infrastructure;
- `FactResolutionResult` persistence;
- workflow-event persistence;
- requirement evaluation;
- policy decision tables;
- `REPRESENT`, `ACCEPT`, or `MANUAL_REVIEW`;
- analyst explanations;
- UI or API expansion;
- extraction coverage for all ten cases.

## 10. Definition of done

The milestone is complete only when:

- CB-2025-0006 uses a new immutable `take-home-v2` Visa 13.3 selection;
- its complete three-file evidence bundle passes the existing hash recheck;
- every file has an immutable converted document;
- only policy-requested evidence facts reach one multimodal model;
- any metadata facts are resolved deterministically (zero for the current
  Visa 13.3 snapshot);
- no evidence-derived successful-path value is hardcoded;
- provider output is treated as untrusted;
- the deterministic adapter and existing admission factory accept the
  complete ordered result;
- the returned immutable result is `READY_FOR_EVALUATION`;
- no evaluation, decision, or recommendation is produced;
- all regression and new tests pass;
- the final report lists every requested fact, its route, status, value
  where present, and provenance.

Approval of this plan authorizes implementation of only the scope above.
