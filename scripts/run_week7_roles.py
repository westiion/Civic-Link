"""H3: frozen BGE plus query/passage role alignment. Only embed calls API."""
import _bootstrap
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import numpy as np
import run_week7_diversity as embedding
import run_week7_references as references
from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from evaluate_week7_bge_direct import direct_order
from week7_hypothesis_support import load_frozen, evaluate_orders, BASELINE

H2 = ROOT/'week7/results/ranking_cases_hypotheses/h2_references'
ROLES = {
    'service': '필요한 민원 서비스나 증명서의 이름, 용도, 증명하는 내용과 다른 서류와의 차이를 안내한다.',
    'documents': '신청할 때 제출하거나 지참할 구비서류, 신청서, 신분증, 위임장과 서류가 필요 없는 경우를 안내한다.',
    'procedure': '신청 방법과 접수 절차, 온라인 또는 방문 신청 경로, 접수기관과 담당기관을 안내한다.',
    'eligibility': '신청 자격과 대상, 본인 또는 대리인 신청 조건, 가족관계에 따른 신청 가능 여부와 제한을 안내한다.',
    'timing': '처리기간, 신청 기한, 발급 가능 시기, 지급 시작 시점과 유효기간을 안내한다.',
    'fees': '발급 수수료, 비용, 납부 방법과 면제 조건을 안내한다.',
    'legal': '민원 업무의 법적 근거, 적용 법령과 조문, 다른 조항에 대한 참조 관계를 설명한다.',
    'scope': '증명서에 표시되는 정보와 공개 범위, 일반과 상세의 차이, 개인정보 열람 제한과 예외를 설명한다.',
}


def cached(out, checksum):
    from week7_result_paths import resolve_result
    out = resolve_result(out)
    protocol, rows = embedding.inputs(out)
    meta = read_json(out/'embedding.run.json')
    path = out/'vectors.sqlite'
    if meta['status'] != 'completed' or digest(path.read_bytes()) != checksum:
        raise ValueError('incomplete or changed embedding cache')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as con:
        identity = json.loads(con.execute("select value from metadata where key='identity'").fetchone()[0])
        if identity != meta['identity'] or identity['inputs_sha256'] != protocol['inputs_sha256']:
            raise ValueError('cache identity mismatch')
        vectors = embedding.read_cache(con, rows)
    if set(vectors) != {r['chunk_id'] for r in rows}: raise ValueError('missing vectors')
    return vectors, identity


def compatible(a, b):
    for key in ('endpoint_sha256', 'model', 'format', 'instruction'):
        if a[key] != b[key]: raise ValueError('incompatible embeddings: '+key)


def prepare(a):
    if a.out.exists(): raise ValueError('output exists; choose a new --out')
    queries, pools, origin = load_frozen()
    docs, identity, checksum = references.source_vectors(references.H1)
    h2protocol, rows = references.check(H2)
    query_hash = read_json(H2/'rank.run.json')['query_vectors_sha256']
    queries_v, query_identity = cached(H2, query_hash)
    compatible(identity, query_identity)
    if {r['chunk_id']:r['text'] for r in rows} != {k:v['question'] for k,v in queries.items()}:
        raise ValueError('query text mismatch')
    if set(queries_v) != set(queries) or not set().union(*map(set,pools.values())) <= set(docs):
        raise ValueError('incomplete source vectors')
    a.out.mkdir(parents=True)
    write_jsonl(a.out/'embedding_inputs.jsonl', [dict(chunk_id=k,text=v,input_sha256=digest(v)) for k,v in ROLES.items()])
    write_json(a.out/'protocol.json', dict(hypothesis='H3 role alignment', baseline_sha256=digest(input_bytes(BASELINE)),
        inputs_sha256=digest(input_bytes(a.out/'embedding_inputs.jsonl')), queries_sha256=origin['queries_sha256'],
        source=str(references.H1), query_source=str(H2), source_vectors_sha256=checksum,
        query_vectors_sha256=query_hash, embedding_identity=identity, dimension=len(next(iter(docs.values()))),
        roles=ROLES, strength=a.strength, temperature=a.temperature, top_k=10,
        policy='minmax BGE + strength * dot(query role softmax, chunk role softmax); control uses mean query profile; full pool preserved',
        selection_uses_gold=False, independent_holdout=False))
    print(f'Prepared {len(queries)} queries; reuse query/chunk vectors. Only {len(ROLES)} role descriptions need API embeddings.')


def check(out):
    protocol, rows = embedding.inputs(out)
    _, _, origin = load_frozen()
    if origin['queries_sha256'] != protocol['queries_sha256']: raise ValueError('queries changed')
    if {r['chunk_id']:r['text'] for r in rows} != protocol['roles']: raise ValueError('roles changed')
    return protocol, rows


def embed(a):
    check(a.out)
    # Same endpoint/model/format and dimensionality checks as H2, without graph-index dependency.
    import os
    p = read_json(a.out/'protocol.json'); identity = p['embedding_identity']
    if digest(os.environ.get('CIVIC_EMBEDDING_URL','').rstrip('/')) != identity['endpoint_sha256'] or os.environ.get('CIVIC_EMBEDDING_MODEL') != identity['model']:
        raise ValueError('use the same company embedding endpoint and model as H1/H2')
    a.format = identity['format']; embedding.embed(a)
    meta = read_json(a.out/'embedding.run.json'); compatible(identity, meta['identity'])
    if meta['dimension'] != p['dimension']: raise ValueError('dimension mismatch')


def profiles(vectors, prototypes, temperature):
    if temperature <= 0: raise ValueError('temperature must be positive')
    v = np.asarray(vectors, dtype=float); p = np.asarray(prototypes, dtype=float)
    if not np.isfinite(v).all() or not np.isfinite(p).all() or np.any(np.linalg.norm(v,axis=1)==0) or np.any(np.linalg.norm(p,axis=1)==0):
        raise ValueError('invalid vectors')
    logits = (v/np.linalg.norm(v,axis=1)[:,None]) @ (p/np.linalg.norm(p,axis=1)[:,None]).T / temperature
    weights = np.exp(logits-logits.max(axis=1,keepdims=True))
    return weights/weights.sum(axis=1,keepdims=True)


def order(scores, alignment, strength):
    base = direct_order(scores); raw = np.array([scores[c] for c in base],dtype=float)
    span = float(np.ptp(raw)); normalized = (raw-raw.min())/span if span else np.ones(len(base))
    traces = {c:dict(bge_rank=i+1,bge_score=scores[c],normalized_bge=float(normalized[i]),
                   role_alignment=float(alignment[c]),role_bonus=float(strength*alignment[c]),
                   final_score=float(normalized[i]+strength*alignment[c])) for i,c in enumerate(base)}
    return sorted(base,key=lambda c:(-traces[c]['final_score'],-scores[c],c)), traces


def rank(a):
    p, rows = check(a.out)
    if (a.out/'rankings.jsonl').exists(): raise ValueError('rankings exist; choose new output for a new experiment')
    docs, identity, checksum = references.source_vectors(Path(p['source']))
    if checksum != p['source_vectors_sha256'] or identity != p['embedding_identity']: raise ValueError('source changed')
    qvectors, qi = cached(Path(p['query_source']),p['query_vectors_sha256']); compatible(identity,qi)
    role_hash = digest((a.out/'vectors.sqlite').read_bytes())
    role_vectors, ri = cached(a.out,role_hash); compatible(identity,ri)
    queries,pools,_ = load_frozen(); role_ids = [r['chunk_id'] for r in rows]
    prototypes = [role_vectors[k] for k in role_ids]
    qids = list(queries); cids = sorted(docs); started = time.perf_counter()
    qp = profiles([qvectors[q] for q in qids],prototypes,p['temperature'])
    cp = profiles([docs[c] for c in cids],prototypes,p['temperature'])
    chunk_profiles = dict(zip(cids,cp)); prior = qp.mean(axis=0)
    write_json(a.out/'role_profiles.json',dict(roles=role_ids,query_profiles={q:qp[i].tolist() for i,q in enumerate(qids)},
        chunk_profiles={c:v.tolist() for c,v in chunk_profiles.items()},mean_query_profile=prior.tolist()))
    results=[]
    for i,qid in enumerate(qids):
        scores=pools[qid]; orders={'bge_direct':direct_order(scores)}; trace={}
        for name,profile in [('role_prior_control',prior),('role_alignment',qp[i])]:
            alignment={c:float(chunk_profiles[c]@profile) for c in scores}
            orders[name],trace[name]=order(scores,alignment,p['strength'])
        results.append(dict(query_id=qid,orders=orders,role_trace=trace))
    write_jsonl(a.out/'rankings.jsonl',results)
    write_json(a.out/'rank.run.json',dict(status='completed',query_count=len(results),api_calls=0,
        source_sha256=digest(Path(__file__).read_bytes()),protocol_sha256=digest(input_bytes(a.out/'protocol.json')),
        predictions_sha256=digest(input_bytes(a.out/'rankings.jsonl')),vectors_sha256=role_hash,
        source_vectors_sha256=checksum,query_vectors_sha256=p['query_vectors_sha256'],
        profiles_sha256=digest(input_bytes(a.out/'role_profiles.json')),seconds=time.perf_counter()-started))
    print(f'Ranked {len(results)} questions; no API calls or BGE inference.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'week7/results/ranking_cases_hypotheses/h3_roles')
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('prepare');s.add_argument('--strength',type=float,default=.15);s.add_argument('--temperature',type=float,default=.05)
    s=sub.add_parser('embed');s.add_argument('--limit',type=int);s.add_argument('--timeout',type=int,default=120)
    sub.add_parser('rank');sub.add_parser('evaluate');a=parser.parse_args()
    if a.command=='prepare' and (not np.isfinite(a.strength) or a.strength<0 or not np.isfinite(a.temperature) or a.temperature<=0):raise ValueError('invalid settings')
    if a.command=='embed' and ((a.limit is not None and a.limit<1) or a.timeout<1):raise ValueError('invalid limit/timeout')
    if a.command=='evaluate':
        check(a.out);meta=read_json(a.out/'rank.run.json')
        for filename,key in [('rankings.jsonl','predictions_sha256'),('protocol.json','protocol_sha256'),('role_profiles.json','profiles_sha256')]:
            if digest(input_bytes(a.out/filename))!=meta[key]:raise ValueError('changed '+filename)
        print(json.dumps(evaluate_orders(read_jsonl(a.out/'rankings.jsonl'),a.out,'bge_direct')['overall'],ensure_ascii=False,indent=2))
    else:globals()[a.command](a)


if __name__=='__main__':main()
