"""Small held-out, full-context CUAD notice extraction check. No training or oracle windows."""
import hashlib
import json
from pathlib import Path
import time

from evaluate import download
from pact.config import Settings
from pact.documents import chunks
from pact.graph import relevant, verify
from pact.model import Model
from pact.retrieval import retrieve
from pact.schema import extraction_schema


def main():
    dataset = json.loads(download('cuad').read_text())['data']
    selected = []
    # Contract-level stable hash partition. Keep this selection fixed, not chosen by model outcome.
    for contract in sorted(dataset,key=lambda d:hashlib.sha256(d['title'].encode()).hexdigest()):
        if int(hashlib.sha256(contract['title'].encode()).hexdigest(),16)%5 != 4:
            continue
        paragraph=contract['paragraphs'][0]
        if len(paragraph['context'])>120000:
            continue
        q=next(q for q in paragraph['qas'] if q['id'].endswith('__Notice Period To Terminate Renewal'))
        positive=bool(q['answers'])
        if sum(bool(x[2]['answers'])==positive for x in selected)<4:
            selected.append((contract,paragraph,q))
        if len(selected)==8:
            break
    model=Model(Settings(mode='demo'))
    results=[]
    for contract,paragraph,q in selected:
        started=time.time()
        clauses=[{**c,'document_id':'cuad','executed':True,'ordinal':1} for c in chunks(paragraph['context'])]
        candidates=retrieve([c for c in clauses if relevant('notice_period',c['text'])],'renewal notice days',3)
        context=[{'clause_id':c['id'],'text':c['text']} for c in candidates]
        try:
            answer=model.call(extraction_schema(['notice_period']),
                'Extract only notice_period required to prevent renewal. Copy the exact duration as value and complete source sentence as quote. '
                'Do not extract notice for breach, payment or unrelated termination. Return an empty items array if absent.',context)
            findings,rejected=verify(answer.items,candidates)
            spans=[(a['answer_start'],a['answer_start']+len(a['text'])) for a in q['answers']]
            supported=sum(any(f['start']<end and f['end']>start for start,end in spans) for f in findings)
            row={'id':q['id'],'gold_present':bool(spans),'predicted':len(findings),'overlapping_gold':supported,
                 'rejected':len(rejected),'correct_abstention':not spans and not findings,'error':None}
        except Exception as exc:
            row={'id':q['id'],'gold_present':bool(q['answers']),'predicted':0,'overlapping_gold':0,
                 'rejected':0,'correct_abstention':False,'error':type(exc).__name__}
        row['seconds']=round(time.time()-started,2)
        results.append(row)
        print(json.dumps(row),flush=True)
    predicted=sum(r['predicted'] for r in results)
    positives=[r for r in results if r['gold_present']]
    negatives=[r for r in results if not r['gold_present']]
    report={'dataset':'CUAD','revision':'a3c393f5d103fd0c516374e4fdff676c8176dcb1','model':model.settings.model,
        'selection':'8 contracts from title-SHA256 modulo-5 holdout bucket 4; 4 positive and 4 negative; source <=120k characters',
        'retrieval':'BM25 top 3 after notice/renewal lexical cue filter; full contract input',
        'cases':results,'span_overlap_precision':sum(r['overlapping_gold'] for r in results)/predicted if predicted else None,
        'positive_case_recall':sum(r['overlapping_gold']>0 for r in positives)/len(positives),
        'negative_abstention_rate':sum(r['correct_abstention'] for r in negatives)/len(negatives),
        'limitations':'Small balanced development diagnostic; overlap is weaker than exact span/value correctness. No legal or production accuracy claim.'}
    Path('reports/cuad-notice-extraction.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))


if __name__=='__main__':
    main()
