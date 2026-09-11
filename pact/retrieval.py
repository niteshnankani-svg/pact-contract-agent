"""Neural caches are bounded and content-addressed; callers supply authorized candidates."""
import hashlib
import json
import math
import re
from collections import Counter, OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import RLock


def tokens(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def retrieve(clauses, query, limit=8):
    bags = [Counter(tokens(c['text'])) for c in clauses]
    average = sum(sum(b.values()) for b in bags) / max(1, len(bags)) or 1
    frequencies = Counter(w for bag in bags for w in bag)
    words = set(tokens(query))
    idf = {w: math.log(1 + (len(bags) - frequencies[w] + .5) / (frequencies[w] + .5)) for w in words}
    scores = [sum(idf[w] * b[w] * 2.5 / (b[w] + 1.5 * (.25 + .75 * sum(b.values()) / average))
                  for w in words if b[w]) for b in bags]
    return [dict(clauses[i], retrieval_score=round(scores[i], 4))
            for i in sorted(range(len(clauses)), key=lambda i: scores[i], reverse=True)[:limit]]


class Neural:
    def __init__(self, profile, embedding_path='', reranker_path=''):
        import torch
        from sentence_transformers import SentenceTransformer, CrossEncoder
        manifest = json.loads((Path(__file__).resolve().parent.parent/'data/manifest.json').read_text())['models']
        prefix = 'compact_' if profile == 'compact' else ''
        embedding, reranker = manifest[prefix+'embedding_candidate'], manifest[prefix+'reranker_candidate']
        torch.set_num_threads(2)
        self.embed = SentenceTransformer(embedding_path or embedding['id'], device='cpu', trust_remote_code=False,
            **({'local_files_only':True} if embedding_path else {'revision':embedding['revision']}))
        self.rerank = CrossEncoder(reranker_path or reranker['id'], device='cpu', trust_remote_code=False, max_length=512,
            **({'local_files_only':True} if reranker_path else {'revision':reranker['revision']}))
        self.embed.max_seq_length = 512
        for network in [self.embed, self.rerank.model]:
            for parameter in network.parameters():
                parameter.data = parameter.data.clone().contiguous()
        self.lock = RLock()
        self.cache = OrderedDict()

    def encode(self, texts):
        import numpy as np
        with self.lock:
            vectors = self.embed.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        if not np.isfinite(vectors).all():
            raise ValueError('Embedding model returned non-finite values')
        return vectors

    def search(self, clauses, query, limit=8):
        import numpy as np
        if not clauses:
            return []
        key = hashlib.sha256(json.dumps([(c['id'],c['text']) for c in clauses]).encode()).hexdigest()
        with self.lock:
            if key not in self.cache:
                self.cache[key] = self.encode([c['text'] for c in clauses])
                while len(self.cache) > 2:
                    self.cache.popitem(last=False)
            vectors = self.cache[key]
            self.cache.move_to_end(key)
        needle = self.encode(['Represent this sentence for searching relevant passages: ' + query])[0]
        dense = np.argsort(-(vectors @ needle))[:30]
        sparse = retrieve(clauses, query, 30)
        by_id = {c['id']:c for c in clauses}
        scores = {}
        for ranking in [[clauses[int(i)]['id'] for i in dense], [c['id'] for c in sparse]]:
            for rank, ident in enumerate(ranking):
                scores[ident] = scores.get(ident, 0) + 1/(60+rank+1)
        candidates = [by_id[i] for i in sorted(scores, key=scores.get, reverse=True)[:30]]
        with self.lock:
            values = self.rerank.predict([(query, c['text']) for c in candidates], batch_size=16, show_progress_bar=False)
        if not np.isfinite(values).all():
            raise ValueError('Reranker returned non-finite values')
        return [{**candidates[int(i)], 'retrieval_score':float(values[int(i)])}
                for i in np.argsort(-values)[:limit]]


@lru_cache(maxsize=1)
def neural(profile='compact', embedding_path='', reranker_path=''):
    return Neural(profile, embedding_path, reranker_path)


def searcher(settings):
    if settings.retrieval == 'hybrid':
        return neural(settings.neural_profile, settings.embedding_path, settings.reranker_path).search
    return retrieve
