import operator
import re
from datetime import timedelta
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from pact.retrieval import searcher
from pact.schema import Plan, extraction_schema
from pact.store import digest, Conflict
from pact.values import renewal_date, notice_days
from pact.policy import assess


class State(TypedDict, total=False):
    tenant: str
    review_id: str
    clauses: list
    plan: dict
    findings: Annotated[list, operator.add]
    rejected: Annotated[list, operator.add]
    proposal: dict
    decision: dict
    task_ids: list


class BranchOutput(TypedDict):
    findings: list
    rejected: list


TOPIC_QUERIES = {
    'notice_period': 'renewal notice days', 'renewal_date': 'renewal date expiration',
    'auto_renewal': 'automatic renewal renews automatically', 'liability': 'liability limitation damages',
    'assignment': 'assignment assign consent', 'confidentiality': 'confidential information disclosure',
}
TOPIC_CUES = {
    'notice_period': r'(?=.*\bnotice\b)(?=.*\b(?:renew\w*|expir\w*)\b)',
    'renewal_date': r'\b(?:renew\w*|expir\w*)\b', 'auto_renewal': r'\bautomat\w*\b',
    'liability': r'\b(?:liabil\w*|damages)\b', 'assignment': r'\bassign\w*\b',
    'confidentiality': r'\bconfidential\w*\b',
}
TOPIC_INSTRUCTIONS = {
    'notice_period': 'Extract the minimum advance notice required to prevent renewal. Copy the duration including the word days into value.',
    'renewal_date': 'Extract the explicit renewal or expiration date. Copy the complete date including year into value. Do not copy a duration.',
    'auto_renewal': 'Extract whether the contract renews automatically. Copy the exact automatic renewal language into value.',
    'liability': 'Extract the liability cap or unlimited liability clause. Copy the exact cap or limit language into value.',
    'assignment': 'Extract the requirement or exception for consent to assignment. Copy the exact relevant language into value.',
    'confidentiality': 'Extract the confidentiality obligation. Copy the exact protection or disclosure language into value.',
}


def relevant(topic, text):
    return bool(re.search(TOPIC_CUES[topic], text, re.I | re.S))


def verify(items, clauses):
    index = {c['id']: c for c in clauses}
    valid, rejected = [], []
    for item in items:
        item = item.model_dump() if hasattr(item, 'model_dump') else item
        clause = index.get(item['clause_id'])
        quote = item['quote']
        if not clause or quote not in clause['text'] or item['value'].lower() not in quote.lower():
            rejected.append({'kind': item['kind'], 'reason': 'Quote or value is not an exact source match'})
            continue
        if not relevant(item['kind'], clause['text']):
            rejected.append({'kind': item['kind'], 'reason': 'Source lacks required topic context'})
            continue
        if item['kind'] == 'renewal_date':
            try:
                renewal_date(item['value'])
            except ValueError:
                rejected.append({'kind': item['kind'], 'reason': 'Renewal date lacks a supported explicit date and year'})
                continue
        if item['kind'] == 'notice_period':
            try:
                notice_days(item['value'])
            except ValueError:
                rejected.append({'kind': item['kind'], 'reason': 'Notice duration is not supported calendar days'})
                continue
        offset = clause['start'] + clause['text'].index(quote)
        finding = {**item, 'document_id': clause['document_id'], 'start': offset,
                   'end': offset + len(quote), 'context':clause['text'], 'executed': clause['executed'], 'ordinal': clause['ordinal']}
        valid.append({**finding, 'id': digest(finding)[:24]})
    return valid, rejected


def proposal_for(review, docs, findings, rejected):
    findings = list({f['id']: f for f in findings}.values())
    executed = [d for d in docs if d['executed']]
    issues = []
    for doc in docs:
        issues.extend(doc.get('input_flags', []))
    if not executed:
        issues.append('No executed agreement is marked. No operative deadline can be created.')
    roots = [d for d in executed if not d.get('supersedes')]
    if len(roots) != 1:
        issues.append('Document precedence is unresolved: require one executed root agreement.')
    seen = set()
    for doc in sorted(executed, key=lambda d: d['ordinal']):
        if doc.get('supersedes') and doc['supersedes'] not in seen:
            issues.append('Executed amendment does not reference an earlier executed source.')
        seen.add(doc['id'])
    children = [d.get('supersedes') for d in executed if d.get('supersedes')]
    if len(children) != len(set(children)):
        issues.append('Competing amendments require a resolved precedence chain.')
    selected = {}
    for f in sorted(findings, key=lambda f: f['ordinal']):
        if not f['executed']:
            continue
        previous = selected.get(f['kind'])
        if previous and f['kind'] in {'notice_period','renewal_date'} and previous['ordinal'] == f['ordinal'] and previous['value'] != f['value']:
            issues.append(f"Conflicting {f['kind']} values in the same source.")
        selected[f['kind']] = f
    for doc in executed:
        for topic in ['notice_period', 'renewal_date']:
            relevant_clauses = [c for c in doc['clauses'] if relevant(topic, c['text'])]
            if topic == 'renewal_date':
                relevant_clauses = [c for c in relevant_clauses if re.search(r'\b(?:19|20)\d{2}\b', c['text'])]
            if relevant_clauses and not any(f['document_id'] == doc['id'] and f['kind'] == topic for f in findings):
                issues.append(f"Unresolved {topic} evidence in executed source: {doc['title']}")
    actions, edits = [], []
    notice, renewal = selected.get('notice_period'), selected.get('renewal_date')
    if notice and renewal:
        try:
            renewal_day = renewal_date(renewal['value'])
            count = notice_days(notice['value'])
        except ValueError:
            renewal_day = None
        if renewal_day:
            if re.search(r'\b(?:no|not)\s+(?:more|earlier|later)\s+than\b',notice.get('context',notice['quote']),re.I):
                issues.append('Notice window wording requires manual interpretation before deadline execution.')
            if re.search(r'\bbusiness\s+days?\b', notice.get('context', notice['quote']), re.I):
                issues.append('Business-day notice requires a supported business calendar.')
            if count < 1 or count > 365:
                issues.append('Notice period falls outside supported bounds.')
            else:
                actions.append({'kind': 'renewal_notice_review', 'title': 'Review renewal and notice obligation',
                    'due_date': str(renewal_day - timedelta(days=count)), 'renewal_date': str(renewal_day),
                    'notice_days': count, 'required_finding_ids': [notice['id'], renewal['id']]})
                if count > review['playbook']['max_notice_days']:
                    edits.append({'finding_id': notice['id'], 'original': notice['quote'],
                        'replacement': review['playbook']['preferred_notice_clause'].replace('{max_notice_days}',str(review['playbook']['max_notice_days'])),
                        'reason': 'Notice period exceeds company playbook; proposed wording requires legal review.'})
        else:
            issues.append('Deadline requires an explicit date with year and unambiguous calendar-day notice period.')
    else:
        issues.append('Missing verified renewal date or notice period; deadline automation is blocked.')
    if rejected:
        issues.append('Model returned unsupported evidence; correct or review extraction before execution.')
    assessments = assess(findings, review['playbook'])
    for assessment in assessments:
        if assessment['status'] == 'deviation':
            actions.append({'kind':assessment['topic']+'_policy_review', 'title':'Resolve '+assessment['topic']+' policy deviation',
                'due_date': actions[0]['due_date'] if actions else None, 'reason':assessment['reason'],
                'required_finding_ids':assessment['finding_ids']})
    result = {'revision': review['revision'], 'findings': findings, 'rejected': rejected,
              'issues': list(dict.fromkeys(issues)), 'blocked': bool(issues), 'actions': actions,
              'redlines': edits, 'coverage': sorted(selected),
              'policy_assessments':assessments, 'scope': 'Renewal operations and evidence-backed policy review tasks; legal interpretation remains with the reviewer.'}
    result['plan_hash'] = digest(result)
    return result


def build_graph(store, model, checkpointer):
    search = searcher(store.settings)
    def call(state, schema, instruction, context, purpose):
        store.reserve_model_call(state['tenant'], state['review_id'], purpose)
        return model.call(schema, instruction, context)
    def event(state, name):
        review = store.get(store.reviews, state['tenant'], state['review_id'])
        store.event(state['tenant'], review['matter_id'], name, review_id=state['review_id'])
        return review

    def prepare(state):
        review = event(state, 'planning')
        docs = [store.get(store.documents, state['tenant'], i) for i in review['document_ids']]
        clauses = [{**c, 'id': d['id'] + ':' + c['id'], 'document_id': d['id'],
                    'executed': d['executed'], 'ordinal': d['ordinal']} for d in docs for c in d['clauses']]
        plan = call(state, Plan, 'Choose contract topics to inspect. Mandatory work covers renewal, notice, liability, assignment and confidentiality.',
                          {'titles': [d['title'] for d in docs], 'playbook': review['playbook']}, 'planning')
        return {'clauses': clauses, 'plan': plan.model_dump()}

    def extract_topic(state, topic, limit=8):
        candidates = [c for c in state['clauses'] if relevant(topic, c['text'])]
        if topic == 'renewal_date':
            candidates = [c for c in candidates if re.search(r'\b(?:19|20)\d{2}\b',c['text'])]
        clauses = search(candidates, TOPIC_QUERIES[topic], limit)
        if not clauses:
            return [], []
        aliases = {f'c{i}':c['id'] for i,c in enumerate(clauses)}
        context = [{'clause_id':f'c{i}', 'text':c['text']} for i,c in enumerate(clauses)]
        result = call(state, extraction_schema([topic]),
            f'Extract ONLY {topic}. '+TOPIC_INSTRUCTIONS[topic]+' Copy the entire source sentence into quote. '
            'Use the supplied clause_id. Return an empty items array when absent.', context, topic)
        for item in result.items:
            item.clause_id = aliases.get(item.clause_id, item.clause_id)
        return verify(result.items, clauses)

    def branch(topics, query):
        def analyze(state):
            event(state, 'analyzing_' + topics[0])
            findings, rejected = [], []
            for topic in topics:
                valid, invalid = extract_topic(state, topic)
                findings.extend(valid)
                rejected.extend(invalid)
            return {'findings': findings, 'rejected': rejected}
        sub = StateGraph(State, output_schema=BranchOutput)
        sub.add_node('retrieve_extract_verify', analyze)
        sub.add_edge(START, 'retrieve_extract_verify')
        sub.add_edge('retrieve_extract_verify', END)
        return sub.compile()

    def propose(state):
        review = event(state, 'reconciling_versions')
        docs = [store.get(store.documents, state['tenant'], i) for i in review['document_ids']]
        proposal = proposal_for(review, docs, state.get('findings', []), state.get('rejected', []))
        store.save_proposal(state['tenant'], state['review_id'], proposal)
        return {'proposal': proposal}

    def recover(state):
        event(state, 'targeted_evidence_recovery')
        found, rejected = [], []
        present = {f['kind'] for f in state.get('findings', []) if f['executed']}
        for topic, query in [('renewal_date', 'renewal date expiration'), ('notice_period', 'renewal notice days')]:
            if topic in present:
                continue
            valid, invalid = extract_topic(state, topic, 3)
            found.extend(valid)
            rejected.extend(invalid)
        return {'findings': found, 'rejected': rejected}

    def join(state):
        return {}

    def approve(state):
        answer = interrupt({'plan_hash': state['proposal']['plan_hash'], 'blocked': state['proposal']['blocked']})
        review = store.get(store.reviews, state['tenant'], state['review_id'])
        if answer != review.get('decision'):
            raise Conflict('Resume payload does not match persisted approval')
        return {'decision': answer}

    def apply(state):
        return {'task_ids': store.execute_actions(state['tenant'], state['review_id'])}

    def finish(state):
        for task_id in state.get('task_ids', []):
            task = store.get(store.tasks, state['tenant'], task_id)
            if task['plan_hash'] != state['proposal']['plan_hash'] or task['status'] != 'open':
                raise Conflict('Task write verification failed')
        status = {'approve': 'completed', 'reject': 'rejected', 'revise': 'needs_changes'}[state['decision']['decision']]
        store.finish_review(state['tenant'], state['review_id'], status, task_ids=state.get('task_ids', []))
        return {}

    graph = StateGraph(State)
    graph.add_node('plan', prepare)
    graph.add_node('commercial', branch(['notice_period', 'renewal_date', 'auto_renewal'], 'renewal expiration notice days automatic'))
    graph.add_node('risk', branch(['liability', 'assignment'], 'liability limitation assignment consent'))
    graph.add_node('confidentiality', branch(['confidentiality'], 'confidential information disclosure'))
    graph.add_node('reconcile', propose)
    graph.add_node('coverage_check', join)
    graph.add_node('recover_evidence', recover)
    graph.add_node('human_approval', approve)
    graph.add_node('apply_tasks', apply)
    graph.add_node('verify_result', finish)
    graph.add_edge(START, 'plan')
    for name in ['commercial', 'risk', 'confidentiality']:
        graph.add_edge('plan', name)
    graph.add_edge(['commercial', 'risk', 'confidentiality'], 'coverage_check')
    graph.add_conditional_edges('coverage_check', lambda s: 'reconcile' if
        {'notice_period', 'renewal_date'}.issubset({f['kind'] for f in s.get('findings', [])}) else 'recover_evidence')
    graph.add_edge('recover_evidence', 'reconcile')
    graph.add_edge('reconcile', 'human_approval')
    graph.add_conditional_edges('human_approval', lambda s: 'apply_tasks' if s['decision']['decision'] == 'approve' else 'verify_result')
    graph.add_edge('apply_tasks', 'verify_result')
    graph.add_edge('verify_result', END)
    return graph.compile(checkpointer=checkpointer)
