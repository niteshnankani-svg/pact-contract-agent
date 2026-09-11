"""Real-provider workflow checks, with source fixtures clearly separate from benchmarks."""
import json
from pathlib import Path
import tempfile
import time

from pact.config import Settings
from pact.documents import chunks
from pact.graph import build_graph
from pact.guardrails import inspect_text
from pact.model import Model
from pact.schema import Playbook
from pact.store import Store
from pact.worker import checkpoint, process


def main():
    started = time.time()
    outcomes = []
    with tempfile.TemporaryDirectory(prefix='pact-live-') as temporary:
        settings = Settings(mode='demo', database_url='sqlite:///'+temporary+'/app.db',
            checkpoint_url=temporary+'/checkpoints.db', artifact_dir=temporary+'/artifacts')
        settings.prepare()
        store = Store(settings)
        matter = store.create_matter('validation','Illustrative SaaS agreement','Procurement',Playbook().model_dump())
        def add(text, parent=None, executed=True):
            return store.add_document('validation',matter['id'],{'title':'Illustrative amendment' if parent else 'Illustrative agreement',
                'kind':'amendment' if parent else 'agreement','supersedes':parent,'executed':executed,'text':text,
                'input_flags':inspect_text(text),'strategy':'clause'}, chunks(text), 'synthetic_validation')
        def run(name, expected_due):
            r = store.create_review('validation',matter['id'],name,'validation-reviewer')
            with checkpoint(settings) as saver:
                process(store,build_graph(store,Model(settings),saver),store.claim('validation'))
            r = store.get(store.reviews,'validation',r['id'])
            p = r['proposal']
            actions = [a for a in p['actions'] if a['kind']=='renewal_notice_review']
            passed = not p['blocked'] and bool(actions) and actions[0]['due_date']==expected_due
            if passed:
                store.decide('validation',r['id'],{'decision':'approve','plan_hash':p['plan_hash'],
                    'note':'Automated validation of known synthetic sources',
                    'confirmed_finding_ids':[f['id'] for f in p['findings']]},'validation-reviewer')
                with checkpoint(settings) as saver:
                    process(store,build_graph(store,Model(settings),saver),store.claim('restarted-validation'))
                tasks = store.list(store.tasks,'validation')
                passed = any(t.get('due_date')==expected_due and t['status']=='open' for t in tasks)
            outcomes.append({'case':name,'passed':passed,'expected_due':expected_due,'blocked':p['blocked'],
                'issues':p['issues'],'verified_topics':p['coverage'],'model_calls':r.get('model_calls',0),
                'task_write_verified':passed})
            print(json.dumps(outcomes[-1]),flush=True)
        agreement=add('The renewal date is 2027-06-30.\n\nEither party may prevent renewal with 90 days written notice.\n\nLiability is limited to fees paid in the previous 12 months.\n\nAssignment requires prior written consent.\n\nBoth parties must protect confidential information.')
        run('original_90_days','2027-04-01')
        add('This amendment replaces the renewal notice provision. Either party may prevent renewal with 60 days written notice. All other terms remain unchanged.',agreement['id'])
        stale = all(t['status']=='stale' for t in store.list(store.tasks,'validation'))
        run('amendment_60_days','2027-05-01')
        result={'model':settings.model,'provider':settings.provider,'real_model_calls':True,
            'fixture_type':'synthetic workflow acceptance cases; not legal benchmark accuracy',
            'cases':outcomes,'passed':all(x['passed'] for x in outcomes),'stale_tasks_invalidated':stale,
            'elapsed_seconds':round(time.time()-started,2)}
        Path('reports/live-workflow.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
