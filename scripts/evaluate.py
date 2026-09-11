"""Download pinned public component data and measure BM25 retrieval on ACORD."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

import httpx

from pact.retrieval import searcher
from pact.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def download(name):
    spec = json.loads((ROOT/'data/manifest.json').read_text())['datasets'][name]
    dest = ROOT/'data/external'/spec['filename']
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        with httpx.stream('GET', spec['url'], follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with dest.with_suffix('.partial').open('wb') as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
        dest.with_suffix('.partial').replace(dest)
    return dest


def acord(profile='lexical', split='test'):
    settings = Settings(mode='demo', retrieval=profile)
    search = searcher(settings)
    path = download('acord')
    with zipfile.ZipFile(path) as archive:
        def read(suffix):
            matches = [n for n in archive.namelist() if n.endswith(suffix) and not n.startswith('__MACOSX/')]
            if len(matches) != 1:
                raise ValueError(f'Ambiguous dataset member: {suffix}')
            return archive.read(matches[0]).decode('utf-8-sig')
        queries = [json.loads(x) for x in read('queries.jsonl').splitlines() if x]
        corpus = [json.loads(x) for x in read('corpus.jsonl').splitlines() if x]
        rows = list(csv.DictReader(io.StringIO(read(split+'.tsv')), delimiter='\t'))
    qrels = {}
    for row in rows:
        qrels.setdefault(row['query-id'], {})[row['corpus-id']] = float(row['score'])
    clauses = [{'id': str(c['_id']), 'text': c['text']} for c in corpus]
    scores = []
    for q in queries:
        truth = qrels.get(str(q['_id']))
        if not truth:
            continue
        found = search(clauses, q['text'], 10)
        gains = [truth.get(x['id'], 0) for x in found]
        ideal = sorted(truth.values(), reverse=True)[:10]
        def dcg(values):
            return sum((2**v-1)/math.log2(i+2) for i,v in enumerate(values))
        scores.append(dcg(gains)/dcg(ideal) if dcg(ideal) else 0)
        if len(scores) % 10 == 0:
            print(f'Evaluated {len(scores)} queries',flush=True)
    result = {'dataset':'ACORD', 'split':split, 'retrieval':profile, 'neural_profile':settings.neural_profile,
              'queries_evaluated':len(scores), 'corpus_size':len(corpus), 'ndcg_at_10':sum(scores)/len(scores),
              'archive_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'limitations':'Unjudged corpus entries scored zero; query IDs may overlap splits. No tuning on this test result. This does not measure agent or legal accuracy.'}
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/f'reports/acord-{profile}-{split}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['acord','download'])
    parser.add_argument('--dataset',choices=['cuad','acord','contract_nli'],default='acord')
    parser.add_argument('--retrieval', choices=['lexical','hybrid'], default='lexical')
    parser.add_argument('--split', choices=['train','valid','test'], default='test')
    args=parser.parse_args()
    if args.command=='acord':
        acord(args.retrieval,args.split)
    else:
        print(download(args.dataset))
