"""A real PostgreSQL/checkpoint integration check, enabled in GitHub Actions."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from pact.api import create_app
from pact.config import Settings
from pact.graph import build_graph
from pact.worker import checkpoint, process
from fastapi.testclient import TestClient
from test_workflow import FixtureModel, setup, decision


@pytest.mark.skipif(not os.getenv('PACT_TEST_POSTGRES'), reason='PostgreSQL test endpoint not configured')
def test_postgres_resume(tmp_path):
    url = os.environ['PACT_TEST_POSTGRES']
    schema = 'test_' + uuid.uuid4().hex
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    scoped = url + ('&' if '?' in url else '?') + 'options=-csearch_path%3D' + schema
    settings = Settings(mode='test', provider='test', database_url=scoped,
        checkpoint_url=scoped.replace('postgresql+psycopg://', 'postgresql://'), artifact_dir=str(tmp_path))
    store = None
    try:
        app = create_app(settings)
        store = app.state.store
        client = TestClient(app, headers={'Authorization':'Bearer demo-admin'})
        matter, _ = setup(client)
        review = client.post(f"/api/matters/{matter['id']}/reviews",headers={'Idempotency-Key':'pg'}).json()
        with checkpoint(settings) as saver:
            process(store,build_graph(store,FixtureModel(),saver),store.claim('pg-first'))
        review = client.get('/api/reviews/'+review['id']).json()
        assert decision(client,review).status_code == 202
        with checkpoint(settings) as saver:
            process(store,build_graph(store,FixtureModel(),saver),store.claim('pg-restart'))
        assert store.list(store.tasks,'demo')[0]['due_date'] == '2027-04-01'
    finally:
        if store:
            store.engine.dispose()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()
