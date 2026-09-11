import math
import re
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path


def tokens(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def retrieve(clauses, query, limit=8):
    """BM25 baseline. No embedding performance claims are made for this profile."""
    bags = [Counter(tokens(c['text'])) for c in clauses]
    average = sum(map(lambda b: sum(b.values()), bags)) / max(1, len(bags)) or 1
    words = set(tokens(query))
    idf = {w: math.log(1 + (len(bags) - sum(w in b for b in bags) + .5) /
                            (sum(w in b for b in bags) + .5)) for w in words}
    scores = [sum(idf[w] * b[w] * 2.5 / (b[w] + 1.5 * (.25 + .75 * sum(b.values()) / average))
                  for w in words if b[w]) for b in bags]
    return [dict(clauses[i], retrieval_score=round(scores[i], 4))
            for i in sorted(range(len(clauses)), key=lambda i: scores[i], reverse=True)[:limit]]


@lru_cache(maxsize=1)
def models():
    from sentence_transformers import SentenceTransformer, CrossEncoder
    manifest = json.loads((Path(__file__).resolve().parent.parent/'data/manifest.json').read_text())['models']
    embedding, reranker = manifest['embedding_candidate'], manifest['reranker_candidate']
    return (SentenceTransformer(embedding['id'], revision=embedding['revision'], device='cpu', trust_remote_code=False),
            CrossEncoder(reranker['id'], revision=reranker['revision'], device='cpu', trust_remote_code=False))


def hybrid(clauses, query, limit=8):
    """Reciprocal-rank fusion followed by a pinned cross-encoder. Explicit opt-in."""
    import numpy as np
    embed, rerank = models()
    vectors = embed.encode([c['text'] for c in clauses], normalize_embeddings=True)
    needle = embed.encode([query], normalize_embeddings=True)[0]
    dense = np.argsort(-(vectors @ needle))[:30]
    sparse = retrieve(clauses, query, 30)
    by_id = {c['id']:c for c in clauses}
    scores = {}
    for ranking in [[clauses[int(i)]['id'] for i in dense], [c['id'] for c in sparse]]:
        for rank, ident in enumerate(ranking):
            scores[ident] = scores.get(ident, 0) + 1/(60+rank+1)
    candidates = [by_id[i] for i in sorted(scores, key=scores.get, reverse=True)[:30]]
    values = rerank.predict([(query, c['text']) for c in candidates])
    return [{**candidates[int(i)], 'retrieval_score':float(values[int(i)])}
            for i in np.argsort(-values)[:limit]]
