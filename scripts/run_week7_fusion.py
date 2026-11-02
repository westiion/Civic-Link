"""H5: frozen BGE and cached query/chunk cosine rank fusion. No API calls."""
import _bootstrap
import argparse
import json
import time
from pathlib import Path
import numpy as np
from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from evaluate_week7_bge_direct import direct_order
from week7_hypothesis_support import load_frozen, evaluate_orders, BASELINE
from run_week7_references import source_vectors, H1
from run_week7_roles import cached, compatible, H2


def sources(document_source,query_source):
    from week7_result_paths import resolve_result
    document_source = resolve_result(document_source)
    query_source = resolve_result(query_source)
    docs,identity,dh=source_vectors(document_source)
    qh=read_json(query_source/'rank.run.json')['query_vectors_sha256']
    qvectors,qi=cached(query_source,qh);compatible(identity,qi)
    queries,pools,origin=load_frozen()
    inputs=read_jsonl(query_source/'embedding_inputs.jsonl')
    if {r['chunk_id']:r['text'] for r in inputs}!={q:r['question'] for q,r in queries.items()}:raise ValueError('query text mismatch')
    if set(qvectors)!=set(queries) or not set().union(*map(set,pools.values()))<=set(docs):raise ValueError('missing vectors')
    dimensions={len(v) for v in list(docs.values())+list(qvectors.values())}
    if len(dimensions)!=1:raise ValueError('embedding dimensions differ')
    return docs,qvectors,identity,dh,qh,queries,pools,origin


def prepare(a):
    if a.out.exists():raise ValueError('output exists; use a new --out')
    docs,qvectors,identity,dh,qh,queries,pools,origin=sources(H1,H2)
    a.out.mkdir(parents=True)
    write_json(a.out/'protocol.json',dict(hypothesis='H5 BGE plus embedding rank fusion',
        baseline_sha256=digest(input_bytes(BASELINE)),queries_sha256=origin['queries_sha256'],
        document_source=str(H1),query_source=str(H2),document_vectors_sha256=dh,query_vectors_sha256=qh,
        embedding_identity=identity,rrf_k=a.rrf_k,embedding_weight=a.embedding_weight,top_k=10,
        policy='full frozen pool; BGE baseline vs cosine-only vs weighted RRF; ties by original BGE rank; no previous hypothesis policies',
        selection_uses_gold=False,independent_holdout=False))
    print(f'Prepared {len(queries)} questions, {len(docs)} cached chunks; no API calls.')


def fuse(scores,cosines,k=60,weight=1.):
    if k<0 or not np.isfinite(weight) or weight<0:raise ValueError('invalid RRF parameters')
    if set(scores)!=set(cosines) or not all(np.isfinite(v) for v in cosines.values()):raise ValueError('invalid cosine scores')
    base=direct_order(scores);br={c:i+1 for i,c in enumerate(base)}
    semantic=sorted(base,key=lambda c:(-cosines[c],br[c]))
    sr={c:i+1 for i,c in enumerate(semantic)}
    trace={c:dict(bge_score=scores[c],bge_rank=br[c],query_cosine=cosines[c],semantic_rank=sr[c],
                 bge_contribution=1/(k+br[c]),semantic_contribution=weight/(k+sr[c]),
                 rrf_score=1/(k+br[c])+weight/(k+sr[c])) for c in base}
    fused=sorted(base,key=lambda c:(-trace[c]['rrf_score'],br[c]))
    return dict(bge_direct=base,embedding_only=semantic,bge_embedding_rrf=fused),trace


def check(out):
    p=read_json(out/'protocol.json');_,_,origin=load_frozen()
    if p['baseline_sha256']!=digest(input_bytes(BASELINE)) or p['queries_sha256']!=origin['queries_sha256']:raise ValueError('frozen inputs changed')
    return p


def rank(a):
    p=check(a.out)
    if (a.out/'rankings.jsonl').exists():raise ValueError('rankings already exist')
    docs,qvectors,identity,dh,qh,queries,pools,_=sources(Path(p['document_source']),Path(p['query_source']))
    if dh!=p['document_vectors_sha256'] or qh!=p['query_vectors_sha256'] or identity!=p['embedding_identity']:raise ValueError('source cache changed')
    start=time.perf_counter();normed={c:v.astype(float)/np.linalg.norm(v.astype(float)) for c,v in docs.items()};results=[]
    for qid in queries:
        q=qvectors[qid].astype(float);q/=np.linalg.norm(q)
        cosines={c:float(np.clip(normed[c]@q,-1,1)) for c in pools[qid]}
        orders,trace=fuse(pools[qid],cosines,p['rrf_k'],p['embedding_weight'])
        results.append(dict(query_id=qid,orders=orders,fusion_trace=trace))
    write_jsonl(a.out/'rankings.jsonl',results)
    write_json(a.out/'rank.run.json',dict(status='completed',api_calls=0,query_count=len(results),
        source_sha256=digest(Path(__file__).read_bytes()),protocol_sha256=digest(input_bytes(a.out/'protocol.json')),
        predictions_sha256=digest(input_bytes(a.out/'rankings.jsonl')),document_vectors_sha256=dh,query_vectors_sha256=qh,
        seconds=time.perf_counter()-start))
    print(f'Ranked {len(results)} questions. No API or BGE inference.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'week7/results/ranking_cases_hypotheses/h5_fusion')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('prepare');s.add_argument('--rrf-k',type=int,default=60);s.add_argument('--embedding-weight',type=float,default=1.)
    sub.add_parser('rank');sub.add_parser('evaluate');a=p.parse_args()
    if a.command=='prepare' and (a.rrf_k<0 or not np.isfinite(a.embedding_weight) or a.embedding_weight<0):raise ValueError('invalid settings')
    if a.command=='evaluate':
        check(a.out);meta=read_json(a.out/'rank.run.json')
        for f,k in [('rankings.jsonl','predictions_sha256'),('protocol.json','protocol_sha256')]:
            if digest(input_bytes(a.out/f))!=meta[k]:raise ValueError('changed '+f)
        print(json.dumps(evaluate_orders(read_jsonl(a.out/'rankings.jsonl'),a.out,'bge_direct')['overall'],ensure_ascii=False,indent=2))
    else:globals()[a.command](a)


if __name__=='__main__':main()
