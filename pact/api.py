import io
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pact.auth import identity, require
from pact.config import Settings
from pact.documents import chunks, extract
from pact.schema import MatterCreate, DocumentCreate, Playbook, Decision, TaskComplete
from pact.store import Store, Conflict, NotFound
from pact.guardrails import BodyLimit, inspect_text


def create_app(settings=None):
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings)
    app = FastAPI(title='Pact · Contract Change & Renewal Agent', version='0.1.0')
    app.state.settings, app.state.store = settings, store
    app.add_middleware(BodyLimit, limit=settings.max_upload_bytes + 100_000)

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def missing(_, exc):
        return JSONResponse({'detail': 'Not found'}, status_code=404)

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        if request.url.path == '/docs':
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready():
        from sqlalchemy import text
        with store.engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return {'status': 'ok'}

    @app.get('/api/me')
    def me(who=Depends(identity)):
        return {**who.__dict__, 'mode': settings.mode, 'model': settings.model, 'retrieval': settings.retrieval}

    @app.get('/api/public-config')
    def public_config():
        return {'demo':settings.mode=='demo'}

    @app.get('/api/matters')
    def matters(who=Depends(identity)):
        return store.list(store.matters, who.tenant)

    @app.post('/api/matters', status_code=201)
    def create(body: MatterCreate, who=Depends(identity)):
        require(who, 'admin', 'reviewer')
        return store.create_matter(who.tenant, body.title, body.owner, Playbook().model_dump())

    @app.get('/api/matters/{ident}')
    def matter(ident: str, who=Depends(identity)):
        result = store.get(store.matters, who.tenant, ident)
        return {**result, 'documents': store.list(store.documents, who.tenant, ident),
                'reviews': store.list(store.reviews, who.tenant, ident),
                'tasks': store.list(store.tasks, who.tenant, ident), 'audit': store.list(store.audit, who.tenant, ident)}

    @app.put('/api/matters/{ident}/playbook')
    def playbook(ident: str, body: Playbook, who=Depends(identity)):
        require(who, 'admin')
        return store.update_playbook(who.tenant, ident, body.model_dump())

    def add(ident, body, who, source):
        require(who, 'admin', 'reviewer')
        try:
            encoder = None
            if body.strategy == 'semantic' and settings.retrieval == 'hybrid':
                from pact.retrieval import neural
                encoder = neural(settings.neural_profile, settings.embedding_path, settings.reranker_path).encode
            clauses = chunks(body.text, body.strategy, encoder)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(body.text) > settings.max_text_chars:
            raise HTTPException(413, 'Extracted text exceeds configured limit')
        return store.add_document(who.tenant, ident, {**body.model_dump(), 'input_flags':inspect_text(body.text)}, clauses, source)

    @app.post('/api/matters/{ident}/documents', status_code=201)
    def document(ident: str, body: DocumentCreate, who=Depends(identity)):
        return add(ident, body, who, 'text')

    @app.post('/api/matters/{ident}/upload', status_code=201)
    async def upload(ident: str, file: UploadFile = File(...), executed: bool = Form(False),
                     kind: str = Form('agreement'), supersedes: str = Form(''), who=Depends(identity)):
        require(who, 'admin', 'reviewer')
        raw = await file.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(413, 'File too large')
        try:
            text = extract(raw, file.filename or '')
            body = DocumentCreate(title=file.filename or 'Document', text=text, executed=executed,
                                  kind=kind, supersedes=supersedes or None)
        except Exception as exc:
            raise HTTPException(422, 'Document could not be parsed or exceeds limits') from exc
        return add(ident, body, who, 'upload')

    @app.post('/api/matters/{ident}/reviews', status_code=202)
    def review(ident: str, idempotency_key: str = Header(..., min_length=1, max_length=150), who=Depends(identity)):
        require(who, 'admin', 'reviewer')
        return store.create_review(who.tenant, ident, idempotency_key, who.subject)

    @app.get('/api/reviews/{ident}')
    def get_review(ident: str, who=Depends(identity)):
        return store.get(store.reviews, who.tenant, ident)

    @app.post('/api/reviews/{ident}/decision', status_code=202)
    def decision(ident: str, body: Decision, who=Depends(identity)):
        require(who, 'admin', 'reviewer')
        store.decide(who.tenant, ident, body.model_dump(), who.subject)
        return store.get(store.reviews, who.tenant, ident)

    @app.get('/api/reviews/{ident}/report.json')
    def report(ident: str, who=Depends(identity)):
        return store.get(store.reviews, who.tenant, ident)

    @app.get('/api/reviews/{ident}/redlines.docx')
    def redlines(ident: str, who=Depends(identity)):
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        review = store.get(store.reviews, who.tenant, ident)
        doc = Document()
        doc.add_heading('Pact — proposed contract changes', 0)
        doc.add_paragraph(f"Review {ident} · revision {review['revision']} · status {review['status']}")
        doc.add_paragraph('Review copy. Proposed wording is not an executed amendment. Original formatting is not preserved.')
        for i, edit in enumerate(review.get('proposal', {}).get('redlines', [])):
            doc.add_heading(edit['reason'], 2)
            p = doc.add_paragraph()
            for tag, text in [('del', edit['original']), ('ins', edit['replacement'])]:
                change = OxmlElement('w:' + tag)
                change.set(qn('w:id'), str(i * 2 + (tag == 'ins')))
                change.set(qn('w:author'), 'Pact proposal')
                run = OxmlElement('w:r')
                node = OxmlElement('w:delText' if tag == 'del' else 'w:t')
                node.text = text
                run.append(node)
                change.append(run)
                p._p.append(change)
        raw = io.BytesIO()
        doc.save(raw)
        raw.seek(0)
        return StreamingResponse(raw, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                 headers={'Content-Disposition': 'attachment; filename="proposed-redlines.docx"'})

    @app.get('/api/operations')
    def operations(who=Depends(identity)):
        require(who, 'admin')
        return store.operations(who.tenant)

    @app.get('/api/tasks')
    def tasks(who=Depends(identity)):
        return store.list(store.tasks,who.tenant)

    @app.post('/api/tasks/{ident}/complete')
    def complete(ident: str, body: TaskComplete, who=Depends(identity)):
        require(who,'admin','reviewer')
        store.complete_task(who.tenant,ident,body.plan_hash,who.subject)
        return store.get(store.tasks,who.tenant,ident)

    web = Path(__file__).resolve().parent.parent / 'web'
    app.mount('/assets', StaticFiles(directory=web), name='assets')

    @app.get('/', include_in_schema=False)
    def home():
        return FileResponse(web / 'index.html')

    return app
