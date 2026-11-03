"""BM25-like lexical retrieval + bounded cosine computation + reciprocal ranks."""
from collections import Counter, defaultdict, deque
import hashlib
import math

from .index import MODEL_VERSION, numpy_module
from .policy import FAMILIES, PURPOSES, allowed
from .text import snippet, tokens


def default_embedder(query):
    # Production-only dependency: do not import at package import or index load time.
    from video_knowledge.engine_client import engine_for
    engine = engine_for(socket_path='/run/imgw-bge/bge.sock',
        embedding_revision='5617a9f61b028005a4858fdac845db406aefb181',
        reranker_revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e')
    engine.timeout = 3.0
    result = engine.encode([query])[0].validated()
    if result.model_version != MODEL_VERSION:
        raise ValueError('向量模型版本不匹配')
    return result.dense


def validate(query, purpose, top_k, families=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('问题须为 1 至 500 字')
    if purpose not in PURPOSES:
        raise ValueError('用途无效')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('条数须为 1 至 20')
    if families is not None and (not isinstance(families, (list, tuple, set)) or not families
                                 or any(f not in FAMILIES for f in families)):
        raise ValueError('资料族无效')


def lexical(snapshot, query, eligible):
    scores = defaultdict(float)
    n, avg = len(snapshot.chunks), max(1, snapshot.average_length)
    for word in set(tokens(query)):
        ids, frequencies = snapshot.postings.get(word, ((), ()))
        idf = math.log(1 + (n - len(ids) + .5) / (len(ids) + .5))
        for i, tf in zip(ids, frequencies):
            if i in eligible:
                scores[i] += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * snapshot.lengths[i] / avg))
    return sorted(scores, key=lambda i: (-scores[i], i))


def rrf(*rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, i in enumerate(ranking, 1):
            scores[i] += 1 / (60 + rank)
    return scores


class Search:
    def __init__(self, index, embedder=default_embedder):
        self.index, self.embedder = index, embedder
        self.last_mode = 'lexical'

    def search(self, query, purpose='owner', top_k=8, families=None, conversation_scope=None):
        validate(query, purpose, top_k, families)
        snapshot = self.index.snapshot()
        if snapshot is None:
            return dict(ready=False, mode='lexical', items=[])
        eligible = {i for i, c in enumerate(snapshot.chunks) if (families is None or c.family in families)
            and allowed(c.family, purpose, confidential=c.confidential, is_cost_price=c.is_cost_price,
                        source_scope=c.scope, conversation_scope=conversation_scope)}
        lex = lexical(snapshot, query, eligible)
        dense, mode = [], 'lexical'
        np = numpy_module() if snapshot.vectors is not None else None
        if np is not None and self.embedder is not None and snapshot.with_vectors and eligible:
            try:
                vector = np.asarray(self.embedder(query), dtype=np.float32)
                norm = np.linalg.norm(vector)
                if vector.shape != (1024,) or not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
                    raise ValueError('查询向量无效')
                vector /= norm
                similarities = {}
                # Only a small working block is float32; the resident matrix stays float16.
                for start in range(0, len(snapshot.chunks), 512):
                    block = snapshot.vectors[start:start+512].astype(np.float32)
                    values = np.einsum('ij,j->i', block, vector)
                    similarities.update((start+j, float(v)) for j, v in enumerate(values)
                                        if start+j in eligible and v >= .35)
                dense = sorted(similarities, key=lambda i: (-similarities[i], i))
                mode = 'hybrid'
            except Exception:
                pass  # No exception, query text or service path is logged.
        self.last_mode = mode
        scores = rrf(lex, dense)
        queues = defaultdict(deque)
        for i in sorted(scores, key=lambda i: (-scores[i], i)):
            queues[snapshot.chunks[i].family].append(i)
        # One result per represented family per round; unused quota is redistributed.
        order = sorted(queues, key=lambda f: -scores[queues[f][0]])
        selected, used_assets = [], Counter()
        consumed = set()
        while len(selected) < top_k and any(queues.values()):
            for family in order:
                while queues[family]:
                    i = queues[family].popleft()
                    c = snapshot.chunks[i]
                    if i in consumed or used_assets[c.asset_id] >= 2:
                        continue
                    # Merge only eligible adjacent hits, never a hidden neighboring chunk.
                    adjacent = [j for j in (i-1, i+1) if j in scores and j not in consumed
                        and snapshot.chunks[j].asset_id == c.asset_id
                        and snapshot.chunks[j].scope == c.scope
                        and snapshot.chunks[j].family == c.family
                        and snapshot.chunks[j].parent == c.parent
                        and abs(snapshot.chunks[j].child - c.child) == 1]
                    group = sorted([i, *adjacent])
                    consumed.update(group)
                    used_assets[c.asset_id] += 1
                    bits = [snippet(snapshot.chunks[j].text, query, 300 // len(group)) for j in group]
                    identifier = hashlib.sha256('\0'.join(snapshot.chunks[j].chunk_id for j in group).encode()).digest()
                    import base64
                    selected.append(dict(rank=len(selected)+1, score=round(scores[i], 8), family=c.family,
                        family_label=FAMILIES[c.family], title=c.title, source_type_label=c.type_label, date=c.date,
                        snippet='\n'.join(bits)[:300], source_id='kb_' + base64.urlsafe_b64encode(identifier).decode().rstrip('='),
                        confidential=any(snapshot.chunks[j].confidential for j in group),
                        is_cost_price=any(snapshot.chunks[j].is_cost_price for j in group)))
                    break
                if len(selected) == top_k:
                    break
        return dict(ready=True, mode=mode, items=selected)
