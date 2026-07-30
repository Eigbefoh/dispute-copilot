# Dispute Copilot

Dispute Copilot is an application for processing payment disputes through a
traceable pipeline of evidence intake, policy evaluation, decisioning, and
analyst review.

For the reproducible v4 setup, operating workflow, live and cached run
instructions, persistence checks, and current limitations, see
[docs/v4-reproducibility-guide.md](docs/v4-reproducibility-guide.md).

The repository now contains an end-to-end, immutable decision pipeline:

```text
dispute-copilot/
├── README.md
├── pyproject.toml
├── src/
│   └── dispute_copilot/
│       ├── domain/
│       ├── intake/
│       ├── extraction/
│       ├── evaluation/
│       ├── recommendation/
│       ├── analyst_review/
│       ├── human_review/
│       ├── orchestration/
│       ├── repositories/
│       └── cli.py
├── data/
│   ├── rulebooks/
│   ├── presentation-rules/
│   └── human-review-rules/
└── tests/
```

## Requirements

- Python 3.11 or newer

## Local setup

Create and activate a virtual environment, then install the package in editable
mode with its development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Verify the installation:

```bash
dispute-copilot --version
```

Create an immutable intake run:

```bash
dispute-copilot CB-2025-0001
```

The orchestration service validates the complete case, verifies every declared
evidence file, records its MIME type, byte size, and SHA-256 digest, and then
creates a uniquely identified `CaseRun` snapshot. The CLI saves that immutable
snapshot to `data/dispute_copilot.db` before reporting it ready for policy
selection.

Retrieve a saved run in a later process:

```bash
dispute-copilot get-run <run-id>
```

Import a fully validated versioned rulebook:

```bash
python -m dispute_copilot.policy.import_rulebook \
  data/rulebooks/take-home-v1
```

The importer verifies source and artifact hashes, validates the canonical
transcript and policies, resolves every exact citation, and inserts the complete
policy batch into SQLite atomically. Re-importing an existing versioned policy
key is rejected.

Import the reviewed, source-aware fact catalogue and updated policies:

```bash
python -m dispute_copilot.policy.import_rulebook \
  data/rulebooks/take-home-v2
```

`take-home-v2` preserves the source PDF and exact transcript citations while
replacing comparison and conclusion-style `required_facts` with 67 atomic
definitions. Every definition has a controlled value type, source type, and
`SINGLE` or `MULTIPLE` cardinality. The importer validates the complete
51-row semantic review, then inserts all eight policies and their definitions
atomically without modifying `take-home-v1`.

Case metadata facts are routed to a deterministic resolver. Only
source-qualified evidence assertions and visual observations are eligible for
the later probabilistic extraction boundary.

Import the cross-case policy-demonstration version:

```bash
python -m dispute_copilot.policy.import_rulebook \
  data/rulebooks/take-home-v3
```

`take-home-v3` keeps all eight policies and adds explicit deterministic policy
aggregation. Visa 13.1 uses `ALL_APPLICABLE`, including a service-only
applicability rule and material address comparisons. Mastercard 4837 uses
`ANY_TWO`: AVS plus CVV is one composite route, successful 3-D Secure is a
second route, and the evaluator exposes satisfied, unresolved, minimum-required
and maximum-possible route counts. The fact catalogue contains 74 reviewed,
source-aware definitions.

Select an exact imported policy for a persisted case run:

```bash
python -m dispute_copilot.cli select-policy <run-id> \
  --rulebook-version take-home-v3
```

Selection reads only immutable SQLite snapshots, writes a separate append-only
`policy_selections` record, and emits `READY_FOR_EXTRACTION`. It does not update
the original `case_runs` row or access source PDFs and evidence files.

Load the exact evidence bytes admitted during intake:

```python
from dispute_copilot.extraction.bundle_loader import load_evidence_bundle

bundle = load_evidence_bundle(selection_id=selection_id)
```

The deterministic loader derives filenames from the immutable case run,
requires `READY_FOR_EXTRACTION`, and rechecks every current file against its
intake SHA-256 before returning a frozen ordered bundle. It performs no document
parsing, OCR, vision, AI, or fact extraction.

Convert a verified bundle into immutable source-preserving documents:

```python
from dispute_copilot.extraction.service import convert_selection_documents

documents = convert_selection_documents(
    selection_id=selection_id,
    vision_provider=configured_vision_provider,
)
```

Text-based PDFs use native extraction and retain one-based page numbers. Visual
files require an explicitly injected vision provider, while PDF pages without
native text require an explicitly injected OCR provider. The provider/model
version is included in `converter_version`. Each `EvidenceDocument` is stored
separately in SQLite and cached by its source SHA-256 plus converter version.
Conversion describes source content only; it does not extract policy-requested
facts or decide whether a requirement is satisfied.

Admit candidate resolved facts to deterministic evaluation:

```python
from dispute_copilot.extraction.fact_resolution_validation import (
    create_fact_resolution_result,
)

result = create_fact_resolution_result(
    selection=selection,
    case_run=case_run,
    catalogue=fact_catalogue,
    evidence_bundle=bundle,
    evidence_documents=documents,
    facts=candidate_facts,
    resolver_version="fact-contract/0.1.0",
)
```

The factory derives the requested facts only from the immutable
`PolicySelection` snapshot. It then enforces exact catalogue value types,
cardinality, metadata/evidence routing, case field paths, document membership,
page identity, extraction method, and supporting text. Candidate values remain
inseparably linked to source-specific provenance. Only a completely validated
result receives `READY_FOR_EVALUATION`; invalid candidate or context data raises
a validation error and produces no result.

Run lean evidence extraction for an immutable selection:

```bash
python -m dispute_copilot.extraction.resolve_facts <selection-id>
```

Rejected provider responses report sanitised field paths, Pydantic error
types, and messages without printing raw model output. For an explicitly local
diagnostic run:

```bash
python -m dispute_copilot.extraction.resolve_facts \
  <selection-id> \
  --debug-provider-response
```

Debug mode writes the redacted raw response to a permission-restricted file in
the operating system's temporary directory. It is never written to SQLite or
the repository, but it may contain evidence-derived output and should be
deleted after local review.

Live model inference is provider-pluggable. The application registers exactly
`nebius`, `openai`, and `anthropic`. Nebius evidence extraction preserves its
tested Chat Completions JSON-schema route. OpenAI uses the native Responses
API, and Anthropic uses the native Messages API with
`output_config.format` JSON Schema output. No generic compatible-provider
route is registered.

The required runtime configuration is:

```text
MODEL_PROVIDER=
MODEL_NAME=
MODEL_API_KEY=
MODEL_BASE_URL=
```

The user must select the exact provider and model. The application has no
default model, performs no automatic model selection, and never substitutes a
provider or model after an error. When `MODEL_BASE_URL` is omitted, each
registered provider receives only its declared default endpoint: Nebius Token
Factory, the OpenAI API, or the Anthropic API. An explicit credential-free
HTTP(S) base URL may be supplied. Missing, unsupported, or incompatible
configuration stops the command before a partial result can be admitted.
Deprecated `NEBIUS_*` variables are accepted only through an isolated
compatibility bridge and must not be mixed with `MODEL_*`.

The service rechecks the admitted evidence hashes, converts or retrieves all
documents from the existing conversion cache, resolves case metadata without a
model, and sends only policy-requested evidence facts to the user-selected
model. During a live run, converted document text and admitted evidence images
are transmitted to the configured provider. The request may be billable under
that provider account.

Every admitted live result records a canonical, non-secret execution identity
in `resolver_version`: provider, exact model, adapter, prompt version, response
schema version, and material request parameters. API credentials are never
included.

The structured response remains untrusted: a deterministic adapter checks
exact catalogue types, requested-fact coverage, document/page identity, source
channel, and supporting text before calling the existing admission factory.
Invalid responses are never partially admitted, silently repaired, or retried
through an unstructured mode.

Provider-facing evidence references form a discriminated union:

- `evidence_type: TEXT` requires `supporting_text` and forbids
  `visual_observation`.
- `evidence_type: VISUAL` requires `visual_observation` and forbids
  `supporting_text`.

Writing printed inside an image remains TEXT evidence even when the page's
recorded conversion method is `VISION`. If writing and visible pixels both
support a value, the provider must return two separate references.

Before fact extraction, every provider converts images through one structured
contract:

- `written_text_observations` preserves visible writing as source assertions;
- `visible_pixel_observations` contains conservative non-text pixel
  observations;
- `limitations` states what the supplied view cannot establish.

The three sections are validated and rendered deterministically into canonical
page text. A delivery photo cannot establish hidden condition, lack of wobble,
absence of hidden damage, complete product matching, or customer return
behaviour. Invalid image output fails closed rather than falling back to a
free-form description.

Provider equivalence means that every provider receives the same admitted
evidence, requested facts, instructions, canonical schemas, and deterministic
admission and decision boundaries. Different live models may still produce
different valid wording or candidate values.

The command prints the admitted fact status and provenance followed by
`READY_FOR_EVALUATION`. A successful result is stored as an immutable,
content-hashed `fact_resolution_results` record. Repeating the command returns
that authoritative result without calling the model again. It does not grade
requirements or produce a dispute recommendation.

Evaluate the persisted result offline:

```bash
python -m dispute_copilot.evaluation.evaluate_requirements \
  <selection-id>
```

Evaluation loads the frozen `PolicySelection`, the authoritative
`FactResolutionResult`, the exact imported policy and its versioned predicate
manifest. It validates all run, selection, policy and content hashes before
interpreting an allow-listed predicate AST with three-valued logic:

- `TRUE` → `SATISFIED`
- `FALSE` → `NOT_SATISFIED`
- `UNKNOWN` → `NOT_ESTABLISHED`
- an inapplicable requirement → `NOT_APPLICABLE`

`NOT_FOUND`, `AMBIGUOUS`, and `CONFLICTING` facts yield `UNKNOWN`; absence of
evidence never proves the opposite. The immutable evaluation and
`READY_FOR_RECOMMENDATION` workflow event are inserted atomically. Repeating
identical evaluation inputs returns the existing result. This command has no
model, network, OCR, PDF, image, or evidence-file path and produces no dispute
recommendation.

Policy-level aggregation is also declarative and versioned. `ALL_APPLICABLE`
excludes `NOT_APPLICABLE` requirements, fails on an explicit applicable
failure, and otherwise remains unresolved if an applicable requirement is not
established. `ANY_TWO` recommends representment once two routes are satisfied,
recommends against representment when
`satisfied_count + unresolved_count < 2`, and otherwise remains insufficient.

Map the persisted evaluation to an analyst-review recommendation:

```bash
python -m dispute_copilot.recommendation.recommend \
  --selection-id <selection-id> \
  --mapping-key default-representment-eligibility-v1 \
  --mapping-version 1.0.0
```

The mapping key and version are mandatory—there is no default or fallback.
The command revalidates evaluation lineage, content hash, policy requirement
order, workflow state, and the recomputed aggregate before loading the frozen,
hashed mapping manifest. It consumes no facts or provenance and has no model,
network, OCR, PDF, image, or evidence-file path.

The immutable recommendation and `READY_FOR_ANALYST_REVIEW` event are written
atomically. Identical replay returns the existing result without creating
another record. This phase produces a recommendation enum and causal
requirement IDs only; it does not generate representment prose or perform any
external submission.

Assemble the completed decision chain for analyst review:

```bash
python -m dispute_copilot.analyst_review.review \
  --recommendation-id <recommendation-id> \
  --presentation-rules-version 1.1.0
```

Pass `--json` to emit the complete structured package. The command follows the
persisted recommendation lineage back through the evaluation, fact resolution,
policy selection, and case run; revalidates their identities and integrity
hashes; and creates one immutable, content-hashed UI snapshot. Requirement
order and outcomes are copied from the selected policy and evaluation.
Evidence gaps come only from persisted unresolved fact bindings, and source
references come only from already-admitted provenance. Visual facts are
explicitly labelled `VISUAL_MODEL_OBSERVATION`.

The rationale uses a frozen, hashed presentation-rules manifest and contains
at most three deterministic sentences. Assembly reads no evidence, policy PDF,
OCR output, model provider, or network resource. An identical replay returns
the existing package. Package creation adds no workflow event, makes no human
decision, and leaves the case at `READY_FOR_ANALYST_REVIEW`.

Review the package in the local human-in-the-loop interface:

```bash
export ANALYST_IDENTITY="vic"
python -m dispute_copilot.human_review.app \
  --review-package-id <review-package-id>
```

The split-screen Gradio interface keeps the advisory recommendation separate
from the final human action. It revalidates every linked source hash before
display, accepts no browser-supplied file path or analyst identity, and records
one immutable `AnalystDecision` plus its workflow event atomically. Confirm,
override and request-more-evidence actions use a frozen, hashed decision-rules
manifest. Closing without deciding writes nothing.

Current evidence provenance has exact document and page references but no
grounded bounding boxes. The interface therefore renders native PDF support as
`PAGE_ONLY` and visual observations as `WHOLE_IMAGE_CONTEXT`. It never
fabricates a precise highlight for the CB-2025-0006 delivery photo. See
`docs/human-review-ui.md` for operation and limitations.

## Multi-case analyst demo

The multi-case demo is a thin UI over the application service. It lists all
cases from `data/cases.json`, creates one independent immutable lineage per
attempt, persists per-case progress, continues after individual failures, and
loads completed review packages without rerunning them.

For a live demo, configure the generic provider variables and trusted local
analyst identity. Keep the credential in an ignored local environment file;
never add it to the repository or command history.

```bash
export MODEL_PROVIDER="<nebius|openai|anthropic>"
export MODEL_NAME="<exact-provider-model-id>"
export MODEL_API_KEY="<provider-api-key>"
# Optional: omit to use the selected provider's registered endpoint.
export MODEL_BASE_URL="<credential-free-provider-api-base-url>"
export ANALYST_IDENTITY="<local-analyst-id>"

python -m dispute_copilot.application.demo \
  --mode live \
  --database data/demo-validation.db
```

Registered endpoint defaults are:

- `nebius`: `https://api.tokenfactory.nebius.com/v1/`
- `openai`: `https://api.openai.com/v1/`
- `anthropic`: `https://api.anthropic.com/`

The configured model must support image input and the adapter's exact
structured-output contract. An incompatible model fails closed; the system
does not select another model or provider.

`Run Selected Case` and `Run All Cases` invoke the same application backend.
Batch execution is sequential. A failed case is recorded as `FAILED` with a
sanitised diagnostic and the next case still runs. Explicit retry creates a new
attempt and a new immutable upstream lineage.

To inspect genuine persisted results without a model provider or network:

```bash
export ANALYST_IDENTITY="<local-analyst-id>"

python -m dispute_copilot.application.demo \
  --mode cached \
  --database data/demo-validation.db
```

Cached mode disables both run controls and labels persisted output as a cached
result. It does not synthesize packages or silently make provider calls.
For an immediately inspectable deterministic fixture-backed presentation, use
the frozen baseline database instead:

```bash
python -m dispute_copilot.application.demo \
  --mode cached \
  --database data/baselines/live-extraction-validation-v1/fixture-demo.db
```

That baseline contains genuine persisted fixture-backed packages for cases
0001–0004; cases 0005–0010 correctly remain `NOT_RUN`. The UI reads this
protected database without adding application tables or changing its bytes.
Successful extraction records expose provider, model, adapter, prompt version,
schema version and non-secret parameters through technical lineage. Request
latency, token usage and estimated cost are currently shown as `NOT RECORDED`.

New application and policy-selection runs default to the corrected
`take-home-v4` pipeline. This selects the v4 policy snapshots, policy-aware
recommendation mapping, suggested analyst actions, and v4 presentation rules
without requiring a rulebook flag. Historical v3 policies and persisted
results remain unchanged and retrievable.

Verify the v4 ten-case behavioural contract offline with:

```bash
python -m pytest -q tests/test_v4_case_behaviour.py
```

Launch a live v4 analyst demo:

```bash
python -m dispute_copilot.application.demo \
  --mode live
```

A completed result can be reopened without a provider call by changing
`--mode live` to `--mode cached`. Both modes default to the isolated
`data/v4-live-validation.db`.

To reproduce the preserved historical pipeline explicitly:

```bash
python -m dispute_copilot.application.demo \
  --mode cached \
  --rulebook-version take-home-v3 \
  --database data/demo-validation.db
```

The known v3 policy limitations remain documented in
`docs/critical-policy-demo-feasibility.json`.

The cross-case acceptance suite proves the same implementation against:

| Case | Policy | Recommendation |
|---|---|---|
| CB-2025-0001 | Visa 13.1 | `RECOMMEND_REPRESENT` |
| CB-2025-0002 | Visa 13.1 | `INSUFFICIENT_EVIDENCE_TO_REPRESENT` |
| CB-2025-0003 | Mastercard 4837 | `RECOMMEND_REPRESENT` |
| CB-2025-0004 | Mastercard 4837 | `RECOMMEND_DO_NOT_REPRESENT` |

These expectations exist only in tests. Production selection, extraction,
evaluation, recommendation and UI code contain no case-ID-specific branch.
See [docs/cross-case-demo.md](docs/cross-case-demo.md) for the automated proof,
live-provider command sequence and causal-pair walkthrough.

`.env.example` contains names and non-secret defaults only. Local `.env` files
and API credentials are excluded by `.gitignore` and must never be committed.

Print the complete run snapshot as JSON:

```bash
dispute-copilot CB-2025-0001 --verbose
```

Run the tests:

```bash
pytest -q
```

## Architecture

- `domain`: Core business entities and data contracts.
- `intake`: Deterministic case and evidence ingestion.
- `orchestration`: Pipeline coordination and state transitions.
- `repositories`: Persistence interfaces and implementations.
- `extraction`: Integrity-checked bundles and source-preserving conversion.
- `evaluation`: Offline three-valued requirement interpretation and
  append-only results.
- `recommendation`: Offline versioned recommendation mapping and analyst-review
  progression.
- `analyst_review`: Offline lineage validation and immutable UI-ready package
  assembly without workflow progression.
- `human_review`: Integrity-checked evidence presentation, local Gradio review,
  and append-only human decisions with atomic workflow events.
- `application`: Stable case catalogue, isolated live execution, persistent
  batch progress, cached retrieval and multi-case demo entry point.
- `data`: Local development data and evidence.
- `tests`: Automated tests.
