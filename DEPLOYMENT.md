# Deployment

## GitHub Actions activation

The CI definition is supplied as `deployment/github-actions.yml.example`. The current GitHub credential lacks `workflow` permission, so the executable workflow could not be published. After authorizing that permission, copy the template to `.github/workflows/ci.yml` and push. It runs lint, SQLite tests, a PostgreSQL checkpoint/resume integration test and a Docker build. These remote checks have not run yet; Docker is unavailable on the local host.

## Local validation

Use `.env.example`, the API command and a separate worker from the README. Only bind demo mode to localhost. Demo SQLite supports one worker. The default configuration fails closed unless you explicitly select demo mode or provide production PostgreSQL and credentials.

## Docker / AWS / Azure / Railway

The same image runs two services: API with its default command and worker with `python -m pact.worker`. Provision PostgreSQL, persistent database storage and an OpenAI-compatible model endpoint. Expose only the API through your cloud TLS ingress. Do not expose PostgreSQL or the model serving endpoint publicly without authentication.

Set these environment variables through your cloud secret manager:

- `PACT_MODE=production`
- `PACT_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`
- `PACT_CHECKPOINT_URL=postgresql://USER:PASSWORD@HOST:5432/DB`
- `PACT_API_KEYS` as a JSON object mapping SHA-256 token digests to `tenant`, `subject`, `role` records.
- `PACT_PROVIDER=openai-compatible`
- `PACT_MODEL`, `PACT_MODEL_URL` (base URL ending in `/v1` where your provider requires it), `PACT_MODEL_API_KEY`.

Generate a token and its digest locally:

```sh
python -c 'import secrets,hashlib,json; t=secrets.token_urlsafe(32); print("TOKEN="+t); print(json.dumps({hashlib.sha256(t.encode()).hexdigest():{"tenant":"company","subject":"reviewer@example.com","role":"admin"}}))'
```

Store the raw token securely and give it only to its intended user. Do not commit it. Use separate identities and roles for reviewers and readers. The API currently uses static bearer credentials, not SSO.

For Compose also set `PACT_DB_PASSWORD` to a random URL-safe password, then `docker compose up --build -d`. API binds to localhost port 8080 for a reverse proxy. Start one API and one worker initially. Application tables are initialized at startup; initialize with a single API replica before scaling. Schema migrations beyond this first schema are not yet implemented.

Railway: create PostgreSQL and two services from the repository's Dockerfile; set the worker start command separately. Configure API target port 8080 and `/health/ready` healthcheck. AWS ECS or Azure Container Apps use the same split service arrangement and a managed PostgreSQL instance.

## Operational checks

`/health/live` checks the process; `/health/ready` checks database connectivity. Authenticated admin `/api/operations` reports job states and worker heartbeat age. Alert on missing worker heartbeats and failed jobs. Put request rate limits, TLS and a hard streaming body limit at ingress. Back up both application and LangGraph checkpoint tables. Test restore before processing real company documents.

Production mode rejects SQLite and empty credential maps, but this is not a substitute for deployment validation. Model qualification, workload testing, tenant data retention/deletion, SSO, database migrations and integration-specific approval policies remain release requirements. No cloud service is deployed by pushing this source repository.
