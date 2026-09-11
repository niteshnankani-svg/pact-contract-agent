# Agent framework

## Graph

```mermaid
flowchart TD
  A[Snapshot source revision and plan] --> B[Commercial subgraph]
  A --> C[Risk subgraph]
  A --> D[Confidentiality subgraph]
  B --> E[Coverage check]
  C --> E
  D --> E
  E -->|Critical evidence missing| F[Targeted retrieval and extraction: max two calls]
  E -->|Critical evidence present| G[Reconcile executed document chain]
  F --> G
  G --> H[Compute deadline and policy redline proposal]
  H --> I[Persist proposal hash and interrupt]
  I -->|Approved matching revision| J[Transactional task upsert]
  I -->|Reject or revise| L[Record reviewer outcome]
  J --> K[Read back and verify task]
```

The three compiled subgraphs run in parallel. Each topic receives its own constrained schema, prompt and relevant candidates. The coverage route permits one recovery pass, with up to two calls for missing critical topics. An invocation uses at most seven initial model calls and two recovery calls; absent-topic candidates skip inference. A persisted per-review call budget also covers retries. Worker attempts are bounded and checkpoints preserve completed supersteps.

## State and provenance

Matter revision, document IDs, exact character offsets, executed status, source ordinal, playbook snapshot, findings, rejected evidence, proposal hash, reviewer identity and task IDs are persisted. LangGraph stores its state in SQLite for a single local worker or PostgreSQL for deployment. Application tables independently enforce action authorization. The model's plan is advisory; mandatory branches always run.

## Input controls

Bearer authorization and tenant scoping apply to all business routes. Roles restrict writes. Upload sizes and text lengths are bounded. DOCX expansion is bounded. PDF scans fail explicitly instead of fabricating OCR. Document content is supplied as untrusted model data. The document parser does not run macros or execute content.

The deadline scope is explicit ISO or named-month dates including year and unambiguous calendar-day notice periods. Numeric and common written durations are supported. Business-day calculations, implied anniversaries and conditional renewal options need additional support; incomplete interpretations block task execution. Source-level input flags and a hard streamed body limit complement schema limits.

## Output controls

Every accepted finding must quote an exact substring and its value must occur inside that quote. This proves provenance, not semantic truth. Reviewers must confirm the action's cited findings. Executed documents require one root and a non-competing ancestry chain. Conflicting values from the same source block execution. Unsigned drafts do not override executed obligations.

Proposal hashes bind the revision, findings, issues, actions and redlines. Approval cannot execute against a changed revision. Worker lease checks fence database mutations. Task IDs are stable per tenant/matter/action kind; task upserts and audit events share a transaction. A second read verifies the stored plan hash and task state. All proposed DOCX edits remain subject to reviewer interpretation.

## Model and retrieval profiles

The local profile uses actual Ollama inference with Gemma 3 1B. It is a functional smoke-test model and is not adequate evidence of legal accuracy. The OpenAI-compatible provider accepts a configured endpoint, model and secret. Qwen is a candidate, not a measured performance claim.

Lexical BM25 is the CPU baseline. The benchmarked compact hybrid profile combines BM25 and BGE-small ranks with reciprocal-rank fusion, then applies MiniLM-L6 cross-encoder reranking. The larger multilingual BGE-M3 profile remains an unqualified option. Neural dependencies and weights are separate from the lightweight container. Clause, fixed and recursive chunkers preserve offsets; semantic grouping uses neural paragraph similarity and preserves source boundaries. See VALIDATION.md for measured results and release gates.

## Failure behavior

Unsupported evidence blocks approval. Stale sources cancel applicable execution. Model/network/schema errors trigger bounded worker retries and then a failed review. A persisted human interrupt resumes through a version-checked API decision. Review rejection and requested revision terminate with distinct statuses; new source input and a new review are needed to proceed.

No arbitrary shell tools, automatic emails, contract signatures or external issue writes are available to the model. Deployment-specific external integrations and semantic legal entailment verification remain future work, not hidden stubs presented as complete features.
