# Pact — Contract Change & Renewal Agent

Pact turns an executed agreement and its amendments into an evidence-backed renewal review. A reviewer approves the exact source revision before the agent creates or updates the renewal task. Adding a source or changing the playbook invalidates old approvals and tasks.

**Built:** FastAPI service, review UI, persistent LangGraph workflow, three analysis subgraphs, bounded evidence recovery, clause/fixed/recursive chunking, exact-span verification, version reconciliation, approval interrupts, idempotent task writes, read-back verification and tracked-change DOCX exports.

**Qualification:** This is a working implementation with tested workflow controls, not a qualified autonomous legal system. Local Gemma 1B inference and the BM25 baseline have documented limitations. See [reports](reports), [agent framework](AGENT_FRAMEWORK.md), and [deployment requirements](DEPLOYMENT.md).

## Run locally

Python 3.12 and an Ollama server with `gemma3:1b` are required for the lightweight profile.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
cp .env.example .env
uvicorn pact.api:create_app --factory --host 127.0.0.1 --port 8092
```

In a second terminal, activate the environment and run `python -m pact.worker`.
Open `http://127.0.0.1:8092`, connect with `demo-admin`, and load the illustrative agreement. Demo credentials work only in demo/test mode. API documentation is at `/docs`; the JSON OpenAPI schema is at `/openapi.json`.

## Review lifecycle

1. Create a matter and upload an agreement. Explicitly mark executed documents and amendment parentage.
2. Start a review with an `Idempotency-Key` header.
3. The worker retrieves and extracts evidence in parallel, retries missing critical evidence once, reconciles source precedence and saves a proposal.
4. Confirm the source findings and approve the matching `plan_hash`. Unsupported or incomplete deadline evidence blocks approval.
5. The resumed graph updates the internal renewal task and reads it back to verify the write.
6. Upload an amendment and start a new review. Prior tasks become stale immediately.

The app does not send legal notices, sign agreements, or silently change executed contracts. Word exports are proposed changes in a new review document, not a format-preserving edit of the uploaded original.

## Tests and real data

```sh
pytest -q
ruff check pact tests scripts
python scripts/evaluate.py acord
python scripts/evaluate.py download --dataset cuad
python scripts/evaluate.py download --dataset contract_nli
```

Pinned dataset revisions and model candidates are in [data/manifest.json](data/manifest.json). Downloaded contracts, runtime records and credentials are excluded from Git. Workflow tests use explicit fixture models; they do not measure LLM accuracy. ACORD retrieval evaluation uses real public relevance judgments.

Optional neural retrieval: install `pip install '.[ml]'`, set `PACT_RETRIEVAL=hybrid`, and provision enough RAM/storage for BGE-M3 plus BGE-reranker-v2-m3. This path uses pinned revisions and has not been locally benchmarked. It does not silently fall back to lexical retrieval.

## Project map

| File | Responsibility |
|---|---|
| `pact/api.py` | Authenticated HTTP API, upload routes, exports, static UI |
| `pact/graph.py` | LangGraph branches, evidence checks, approval and execution |
| `pact/store.py` | Tenant-scoped state, revision locks, job leases, audit and task transactions |
| `pact/worker.py` | Durable queue processing, checkpoint recovery and heartbeat |
| `pact/model.py` | Real Ollama / OpenAI-compatible structured inference |
| `pact/documents.py` | Native text/PDF/DOCX parsing and source-offset chunks |
| `pact/retrieval.py` | BM25 and optional neural retrieval/reranking |
| `web/` | Review workspace, source confirmation, obligations and activity |
| `tests/` | Workflow, authorization, evidence and export checks |
| `scripts/evaluate.py` | Pinned public-data download and ACORD benchmark |
| `APP_FRAMEWORK.md` | Product scope and acceptance criteria |
| `AGENT_FRAMEWORK.md` | Agent graph, state, guardrails and limits |

GitHub hosts the source and CI. A running API needs the API container, worker container, PostgreSQL and a model endpoint on your chosen cloud.
