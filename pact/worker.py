"""One worker process per deployment replica; PostgreSQL claims coordinate replicas."""
import logging
import signal
import threading
import time
import uuid
from contextlib import contextmanager

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from pact.config import Settings
from pact.graph import build_graph
from pact.model import Model
from pact.store import Store, Conflict, active_job

log = logging.getLogger('pact.worker')


@contextmanager
def checkpoint(settings):
    if settings.checkpoint_url.startswith('postgresql://'):
        from langgraph.checkpoint.postgres import PostgresSaver
        with PostgresSaver.from_conn_string(settings.checkpoint_url) as saver:
            saver.setup()
            yield saver
    else:
        with SqliteSaver.from_conn_string(settings.checkpoint_url) as saver:
            yield saver


def process(store, graph, job):
    tenant, ident = job['tenant'], job['id']
    config = {'configurable': {'thread_id': tenant + ':' + ident}, 'recursion_limit': 30}
    review = store.get(store.reviews, tenant, ident)
    if review['status'] == 'stale':
        store.finish_job(job, 'cancelled')
        return
    state = graph.get_state(config)
    payload = (Command(resume=job['payload']['resume']) if job['payload'].get('resume') and state.interrupts
               else None if state.values else {'tenant': tenant, 'review_id': ident, 'findings': [], 'rejected': []})
    token = active_job.set(job)
    try:
        graph.invoke(payload, config)
    finally:
        active_job.reset(token)
    state = graph.get_state(config)
    store.finish_job(job, 'waiting' if state.interrupts else 'completed')


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    settings.prepare()
    store = Store(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = str(uuid.uuid4())
    with checkpoint(settings) as saver:
        graph = build_graph(store, Model(settings), saver)
        while not stop.is_set():
            store.heartbeat(worker)
            job = store.claim(worker)
            if not job:
                stop.wait(1)
                continue
            done = threading.Event()

            def keepalive():
                while not done.wait(15):
                    store.heartbeat(worker)
                    if not store.renew(job):
                        return

            thread = threading.Thread(target=keepalive, daemon=True)
            thread.start()
            try:
                process(store, graph, job)
            except Conflict:
                store.finish_job(job, 'cancelled')
            except Exception as exc:
                log.error('Review %s failed (%s)', job['id'], type(exc).__name__)
                store.event(job['tenant'], store.get(store.reviews, job['tenant'], job['id'])['matter_id'],
                            'worker_error', error_type=type(exc).__name__, review_id=job['id'])
                store.finish_job(job, 'queued')
                time.sleep(1)
            finally:
                done.set()
                thread.join(timeout=2)


if __name__ == '__main__':
    main()
