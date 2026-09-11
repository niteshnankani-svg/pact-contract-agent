# Validation and remaining release gates

This file distinguishes executed checks from implemented but unqualified paths. Test fixtures are not counted as real contract benchmark successes.

## Executed

- SQLite/API tests cover durable approval/resume, source invalidation, idempotent task updates, task completion, tenant isolation, roles, proposal hashes, stale leases, model-call budgets, streamed upload limits, hostile input flags, exact source spans, typed dates/durations, policy deviations, provider error handling and Word tracked-change structure.
- Isolated PostgreSQL testing exercised persistent checkpoints and approval resume against a real PostgreSQL server. Docker was not needed for this database check.
- `reports/live-workflow.json`: actual Ollama Gemma inference passed the illustrative original-contract and amendment workflows, including reviewer approval, worker restart, task read-back and deadline change from 2027-04-01 to 2027-05-01.
- `reports/acord-hybrid-test.json`: compact BGE + cross-encoder retrieval ran over 3,931 public clauses and 57 test queries. nDCG@10 was 0.135732, versus the earlier lexical baseline of 0.096422. Both are low absolute scores; unjudged results count as zero.
- `reports/semantic-chunking.json`: real compact-model embeddings grouped source paragraphs while preserving exact character offsets. This is a functional chunking check, not a retrieval-quality claim.
- `reports/cuad-notice-extraction.json`: a fixed 8-contract full-context diagnostic produced 0/4 positive-case recall and 3/4 negative abstentions with Gemma 1B. Validation errors are counted as failures. This result fails model qualification.

## Changes after the first build

Topic-specific schemas and prompts replace the shared multi-topic extraction prompt. Candidate filtering excludes date-free renewal clauses from explicit-date extraction. Values are checked independently of citation existence. The graph checks critical evidence coverage for each executed source, including amendments. Model-call budgets persist across retries. Compact neural retrieval uses bounded caches, pinned model revisions and finite-output checks. Semantic paragraph grouping is available when neural retrieval is configured. Policy deviations create source-linked review tasks.

The old `reports/local-model-validation.json` is retained as historical evidence of the original failure. It is superseded for the synthetic workflow by `reports/live-workflow.json`, not for general legal accuracy.

## Remaining gates that need external inputs

1. **Model qualification:** configure a stronger hosted endpoint and rerun real-contract evaluation. The local 1B model is unsuitable for broad contract extraction. The current host ran out of writable disk space during validation; only the reproducible CUAD download created by this task was removed, after its evaluation report was saved. No user documents were deleted.
2. **Container execution:** Docker is not installed locally. The ready GitHub Actions template builds the image, but the current GitHub credential lacks `workflow` permission. Authorize that permission to activate the workflow and run Linux/container checks.
3. **Customer acceptance:** representative company agreements and the company's reviewed playbook are necessary to qualify operational coverage. Public component datasets do not supply the company's policies, executed amendments or approval rules.

No result in this repository establishes legal enforceability, enterprise security certification, or a cloud production deployment.
