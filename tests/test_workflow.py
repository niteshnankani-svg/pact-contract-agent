import hashlib
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from pact.api import create_app
from pact.config import Settings
from pact.documents import chunks
from pact.graph import build_graph, verify
from pact.schema import Extraction, Plan
from pact.worker import process


class FixtureModel:
    def call(self, schema, instruction, context):
        if schema is Plan:
            return Plan(topics=['notice_period', 'renewal_date'], rationale='Fixture test plan')
        items = []
        if 'notice_period' in instruction:
            for c in context:
                for kind, value in [('notice_period', '90 days'), ('notice_period', '60 days'),
                                    ('renewal_date', '2027-06-30')]:
                    if value in c['text']:
                        items.append(dict(kind=kind, value=value, quote=c['text'].strip(), clause_id=c['clause_id']))
        return Extraction(items=items)


@pytest.fixture
def env(tmp_path):
    settings = Settings(mode='test', provider='test', database_url=f'sqlite:///{tmp_path}/app.db',
                        checkpoint_url=str(tmp_path/'checkpoints.db'), artifact_dir=str(tmp_path/'artifacts'),
                        api_keys={hashlib.sha256(b'other').hexdigest():{'tenant':'other','subject':'tester','role':'admin'}})
    app = create_app(settings)
    client = TestClient(app, headers={'Authorization':'Bearer demo-admin'})
    yield client, app.state.store, settings


def source(client, matter, days=90, parent=None, executed=True):
    response = client.post(f'/api/matters/{matter}/documents', json={
        'title':'Agreement' if not parent else 'Amendment', 'kind':'amendment' if parent else 'agreement',
        'executed':executed, 'supersedes':parent,
        'text':f'The renewal date is 2027-06-30.\n\nEither party may prevent renewal with {days} days written notice.'})
    assert response.status_code == 201, response.text
    return response.json()


def setup(client):
    matter = client.post('/api/matters', json={'title':'Vendor contract','owner':'Procurement'}).json()
    doc = source(client, matter['id'])
    return matter, doc


def run(client, store, settings, matter, key='one'):
    response = client.post(f"/api/matters/{matter['id']}/reviews", headers={'Idempotency-Key':key})
    assert response.status_code == 202, response.text
    review = response.json()
    with SqliteSaver.from_conn_string(settings.checkpoint_url) as saver:
        process(store, build_graph(store, FixtureModel(), saver), store.claim('test'))
    return client.get('/api/reviews/'+review['id']).json()


def decision(client, review, action='approve'):
    return client.post('/api/reviews/'+review['id']+'/decision', json={
        'decision':action, 'plan_hash':review['proposal']['plan_hash'], 'note':'Verified source evidence',
        'confirmed_finding_ids':[f['id'] for f in review['proposal']['findings']]})


def test_durable_approval_and_amendment(env):
    client, store, settings = env
    matter, doc = setup(client)
    review = run(client, store, settings, matter)
    assert review['status'] == 'awaiting_approval'
    assert not review['proposal']['blocked']
    assert review['proposal']['actions'][0]['due_date'] == '2027-04-01'
    assert store.list(store.tasks, 'demo') == []
    assert decision(client, review).status_code == 202
    assert decision(client, review).status_code == 409
    with SqliteSaver.from_conn_string(settings.checkpoint_url) as saver:
        process(store, build_graph(store, FixtureModel(), saver), store.claim('restarted-worker'))
    task = store.list(store.tasks, 'demo')[0]
    assert task['due_date'] == '2027-04-01'
    assert client.get('/api/reviews/'+review['id']).json()['status'] == 'completed'
    store.execute_actions('demo', review['id'])
    assert len(store.list(store.tasks, 'demo')) == 1
    source(client, matter['id'], 60, doc['id'])
    assert store.list(store.tasks, 'demo')[0]['status'] == 'stale'
    assert decision(client, review).status_code == 409
    next_review = run(client, store, settings, matter, 'two')
    assert next_review['proposal']['actions'][0]['due_date'] == '2027-05-01'
    assert decision(client, next_review).status_code == 202
    with SqliteSaver.from_conn_string(settings.checkpoint_url) as saver:
        process(store, build_graph(store, FixtureModel(), saver), store.claim('again'))
    assert len(store.list(store.tasks, 'demo')) == 1
    assert store.list(store.tasks, 'demo')[0]['due_date'] == '2027-05-01'


def test_tenant_isolation_and_roles(env):
    client, _, _ = env
    matter, _ = setup(client)
    assert client.get('/api/matters/'+matter['id'], headers={'Authorization':'Bearer other'}).status_code == 404
    assert client.get('/api/matters', headers={'Authorization':'Bearer other'}).json() == []
    assert client.post('/api/matters', json={'title':'bad','owner':'reader'},
                       headers={'Authorization':'Bearer demo-reader'}).status_code == 403
    assert client.get('/api/matters', headers={'Authorization':''}).status_code == 401


def test_idempotency_and_stale_key(env):
    client, store, settings = env
    matter, doc = setup(client)
    review = run(client, store, settings, matter)
    again = client.post(f"/api/matters/{matter['id']}/reviews",headers={'Idempotency-Key':'one'})
    assert again.json()['id'] == review['id']
    source(client, matter['id'], 60, doc['id'])
    assert client.post(f"/api/matters/{matter['id']}/reviews",headers={'Idempotency-Key':'one'}).status_code == 409


def test_draft_blocks_deadline(env):
    client, store, settings = env
    matter = client.post('/api/matters',json={'title':'Draft agreement','owner':'Legal'}).json()
    source(client,matter['id'],executed=False)
    review = run(client,store,settings,matter)
    assert review['proposal']['blocked']
    assert decision(client,review).status_code == 409


def test_missing_confirmation_and_bad_hash(env):
    client, store, settings = env
    matter, _ = setup(client)
    review = run(client,store,settings,matter)
    body={'decision':'approve','plan_hash':review['proposal']['plan_hash'],'note':'Confirmed','confirmed_finding_ids':[]}
    assert client.post('/api/reviews/'+review['id']+'/decision',json=body).status_code == 409
    body['plan_hash']='0'*64
    assert client.post('/api/reviews/'+review['id']+'/decision',json=body).status_code == 409


def test_exact_evidence_rejects_hallucination():
    c={'id':'c1','text':'Notice requires 90 days.','start':0,'document_id':'d1','executed':True,'ordinal':1}
    item={'kind':'notice_period','clause_id':'c1','quote':c['text'],'value':'30 days'}
    valid, rejected=verify([item],[c])
    assert not valid and rejected
    item['value']='90 days'
    valid,_=verify([item],[c])
    assert c['text'][valid[0]['start']:valid[0]['end']]==valid[0]['quote']


@pytest.mark.parametrize('strategy',['clause','fixed','recursive'])
def test_chunk_offsets(strategy):
    text=('First paragraph. '*250)+'\n\nSecond paragraph.'
    result=chunks(text,strategy)
    assert all(text[c['start']:c['end']]==c['text'] for c in result)
    assert all(len(c['text'])<=3000 for c in result)


def test_redlines_are_tracked_changes(env):
    client,store,settings=env
    matter,_=setup(client)
    review=run(client,store,settings,matter)
    response=client.get('/api/reviews/'+review['id']+'/redlines.docx')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml=archive.read('word/document.xml')
        assert b'<w:del ' in xml and b'<w:ins ' in xml


def test_production_requires_credentials_and_postgres():
    with pytest.raises(ValueError):
        Settings().prepare()


def test_lease_fences_expired_worker(env):
    from pact.store import active_job, Conflict
    from sqlalchemy import update
    client, store, _ = env
    matter, _ = setup(client)
    review = client.post(f"/api/matters/{matter['id']}/reviews",headers={'Idempotency-Key':'lease'}).json()
    job = store.claim('old-worker')
    with store.tx() as conn:
        conn.execute(update(store.jobs).where(store.jobs.c.id == job['id']).values(lease_until=0))
    new_job = store.claim('new-worker')
    assert new_job['lease'] != job['lease']
    token = active_job.set(job)
    try:
        with pytest.raises(Conflict,match='lease'):
            store.save_proposal('demo',review['id'],{'plan_hash':'a'*64})
    finally:
        active_job.reset(token)


@pytest.mark.parametrize('choice,expected',[('reject','rejected'),('revise','needs_changes')])
def test_nonapproval_creates_no_tasks(env,choice,expected):
    client,store,settings=env
    matter,_=setup(client)
    review=run(client,store,settings,matter)
    assert decision(client,review,choice).status_code==202
    with SqliteSaver.from_conn_string(settings.checkpoint_url) as saver:
        process(store,build_graph(store,FixtureModel(),saver),store.claim('decision'))
    assert client.get('/api/reviews/'+review['id']).json()['status']==expected
    assert store.list(store.tasks,'demo')==[]
