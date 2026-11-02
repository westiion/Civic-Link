"""Week7 hypothesis 2: fixed-pool REFERENCES selection by query cosine.

Reuses H1 document vectors, embeds only queries, never runs BGE locally.
"""
import _bootstrap
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
import run_week7_diversity as embedding
import numpy as np
from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from evaluate_week7_bge_direct import direct_order
from week7_hypothesis_support import load_frozen, corpus, evaluate_orders, DB, BASELINE, SOURCE

H1=ROOT/'week7/results/ranking_cases_hypotheses/h1_diversity'


def reference_index(graph,evidence):
    nodes={n['id']:n for n in graph['nodes']};index={}
    for edge in graph['edges']:
        if edge['relation']!='REFERENCES' or nodes[edge['target']]['type']!='Article':continue
        targets=sorted({c for eid in evidence['node_bindings'].get(edge['target'],[])
            if evidence['records'][eid]['role']=='article_context' for c in evidence['records'][eid]['chunk_ids']})
        for eid in evidence['edge_bindings'].get(edge['key'],[]):
            rec=evidence['records'][eid]
            for cid in rec['chunk_ids']:
                index.setdefault(cid,[]).append(dict(edge_id=edge['key'],source_node=edge['source'],
                    target_node=edge['target'],target_chunks=targets,evidence_id=eid,quote=rec['quote'],
                    document_id=rec['source_document_id'],start_char=rec['start_char'],end_char=rec['end_char']))
    return index


def source_vectors(source):
    from week7_result_paths import resolve_result
    source = resolve_result(source)
    protocol,rows=embedding.inputs(source)
    meta=read_json(source/'embedding.run.json');run=read_json(source/'rank.run.json')
    if meta['status']!='completed':raise ValueError('H1 embeddings incomplete')
    path=source/'vectors.sqlite'
    if digest(path.read_bytes())!=run['vectors_sha256']:raise ValueError('H1 vector cache changed')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        identity=json.loads(c.execute("select value from metadata where key='identity'").fetchone()[0])
        if identity!=meta['identity'] or identity['inputs_sha256']!=protocol['inputs_sha256']:
            raise ValueError('H1 vector identity differs')
        vectors=embedding.read_cache(c,rows)
    if set(vectors)!={r['chunk_id'] for r in rows}:raise ValueError('missing H1 vectors')
    return vectors,identity,run['vectors_sha256']


def prepare(a):
    if a.out.exists():raise ValueError('choose a new --out; existing results are preserved')
    queries,pools,protocol=load_frozen();corpus(a.db,protocol)
    vectors,identity,checksum=source_vectors(a.source)
    if not set().union(*(set(p) for p in pools.values()))<=set(vectors):raise ValueError('H1 vectors do not cover pools')
    gp=ROOT/'data/graph/graph.json';ep=gp.with_suffix('.evidence.json');origin=read_json(SOURCE/'h3.run.json')
    for path,key in [(gp,'graph_sha256'),(ep,'evidence_sha256')]:
        if digest(input_bytes(path))!=origin[key]:raise ValueError('graph differs from baseline')
    rows=[dict(chunk_id=qid,text=q['question'],input_sha256=digest(q['question'])) for qid,q in queries.items()]
    a.out.mkdir(parents=True)
    write_jsonl(a.out/'embedding_inputs.jsonl',rows)
    write_json(a.out/'reference_index.json',reference_index(read_json(gp),read_json(ep)))
    write_json(a.out/'protocol.json',dict(hypothesis='Week7 H2 semantic explicit-reference selection',
        baseline_sha256=digest(input_bytes(BASELINE)),inputs_sha256=digest(input_bytes(a.out/'embedding_inputs.jsonl')),
        queries_sha256=protocol['queries_sha256'],source=str(a.source.resolve()),source_vectors_sha256=checksum,
        embedding_identity=identity,dimension=len(next(iter(vectors.values()))),
        graph_sha256=origin['graph_sha256'],evidence_sha256=origin['evidence_sha256'],
        index_sha256=digest(input_bytes(a.out/'reference_index.json')),slots=a.slots,top_k=10,
        policy='protect BGE top(10-slots); direct outgoing REFERENCES evidenced by protected chunks; same pool only; at most one chunk per target Article; BGE vs query-cosine selection',
        independent_holdout=False,selection_uses_gold=False))
    print(f'Prepared {len(queries)} queries; reusing {len(vectors)} H1 document vectors; no API calls.')


def check(out):
    protocol,rows=embedding.inputs(out)
    if digest(input_bytes(out/'reference_index.json'))!=protocol['index_sha256']:raise ValueError('reference index changed')
    _,_,origin=load_frozen()
    if origin['queries_sha256']!=protocol['queries_sha256']:raise ValueError('queries changed')
    return protocol,rows


def embed(a):
    protocol,_=check(a.out);identity=protocol['embedding_identity']
    endpoint=os.environ.get('CIVIC_EMBEDDING_URL','').rstrip('/')
    if digest(endpoint)!=identity['endpoint_sha256'] or os.environ.get('CIVIC_EMBEDDING_MODEL')!=identity['model']:
        raise ValueError('use the same CIVIC_EMBEDDING_URL and MODEL as H1 to keep vectors compatible')
    a.format=identity['format']
    embedding.embed(a)
    meta=read_json(a.out/'embedding.run.json')
    if meta['dimension']!=protocol['dimension']:raise ValueError('query/document dimensions differ')


def select(scores,similarities,index,slots=3,semantic=False):
    if not 0<=slots<=5:raise ValueError('slots must be 0..5')
    base=direct_order(scores);fixed=base[:10-slots]
    if slots==0:return base,dict(eligible=[],selected=[])
    eligible=[]
    for anchor in fixed:
        for edge in index.get(anchor,[]):
            targets=[c for c in edge['target_chunks'] if c in scores and c!=anchor]
            if set(targets)&set(fixed):continue
            for cid in targets:
                eligible.append(dict(**edge,anchor_chunk=anchor,anchor_rank=base.index(anchor)+1,
                    chunk_id=cid,bge_rank=base.index(cid)+1,bge_score=scores[cid],query_cosine=similarities[cid]))
    eligible.sort(key=lambda r:(-r['query_cosine'] if semantic else -r['bge_score'],
                                -r['bge_score'],r['chunk_id'],r['anchor_rank'],r['edge_id']))
    selected=[];seen=set(fixed);targets=set()
    for row in eligible:
        if row['chunk_id'] in seen or row['target_node'] in targets:continue
        selected.append(row);seen.add(row['chunk_id']);targets.add(row['target_node'])
        if len(selected)==slots:break
    head=fixed+[r['chunk_id'] for r in selected]
    return head+[c for c in base if c not in set(head)],dict(eligible=eligible,selected=selected)


def rank(a):
    protocol,rows=check(a.out)
    if (a.out/'rankings.jsonl').exists():raise ValueError('rankings already exist')
    vectors,identity,checksum=source_vectors(Path(protocol['source']))
    if checksum!=protocol['source_vectors_sha256'] or identity!=protocol['embedding_identity']:raise ValueError('H1 vectors changed')
    meta=read_json(a.out/'embedding.run.json')
    if meta['status']!='completed' or meta['identity']['inputs_sha256']!=protocol['inputs_sha256']:
        raise ValueError('complete query embedding run first')
    for key in ('endpoint_sha256','model','format','instruction'):
        if meta['identity'][key]!=identity[key]:raise ValueError('incompatible query embeddings')
    with closing(sqlite3.connect((a.out/'vectors.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as c:
        cached_identity=json.loads(c.execute("select value from metadata where key='identity'").fetchone()[0])
        if cached_identity!=meta['identity']:raise ValueError('query cache provenance differs')
        query_vectors=embedding.read_cache(c,rows)
    queries,pools,_=load_frozen()
    if set(query_vectors)!=set(queries):raise ValueError('incomplete query vectors')
    if any(len(v)!=protocol['dimension'] for v in query_vectors.values()):raise ValueError('dimension mismatch')
    normed={cid:v.astype(float)/np.linalg.norm(v.astype(float)) for cid,v in vectors.items()}
    index=read_json(a.out/'reference_index.json');results=[];started=time.perf_counter()
    for i,qid in enumerate(queries,1):
        q=query_vectors[qid].astype(float);q/=np.linalg.norm(q)
        similarities={cid:float(np.clip(normed[cid]@q,-1,1)) for cid in pools[qid]}
        orders={'bge_direct':direct_order(pools[qid])};trace={}
        for name,semantic in [('references_bge',False),('references_semantic',True)]:
            orders[name],trace[name]=select(pools[qid],similarities,index,protocol['slots'],semantic)
        results.append(dict(query_id=qid,orders=orders,reference_trace=trace))
        if i%50==0 or i==len(queries):print(f'Ranked {i}/{len(queries)}',flush=True)
    write_jsonl(a.out/'rankings.jsonl',results)
    write_json(a.out/'rank.run.json',dict(status='completed',query_count=len(results),api_calls=0,
        source_sha256=digest(Path(__file__).read_bytes()),protocol_sha256=digest(input_bytes(a.out/'protocol.json')),
        predictions_sha256=digest(input_bytes(a.out/'rankings.jsonl')),
        query_vectors_sha256=digest((a.out/'vectors.sqlite').read_bytes()),source_vectors_sha256=checksum,
        seconds=time.perf_counter()-started))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'week7/results/ranking_cases_hypotheses/h2_references')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('prepare');s.add_argument('--source',type=Path,default=H1);s.add_argument('--db',type=Path,default=DB)
    s.add_argument('--slots',type=int,choices=range(0,6),default=3)
    s=sub.add_parser('embed');s.add_argument('--limit',type=int);s.add_argument('--timeout',type=int,default=120)
    sub.add_parser('rank');sub.add_parser('evaluate');a=p.parse_args()
    if a.command=='embed' and ((a.limit is not None and a.limit<1) or a.timeout<1):raise ValueError('invalid limit/timeout')
    if a.command=='evaluate':
        check(a.out);meta=read_json(a.out/'rank.run.json')
        if meta['status']!='completed' or digest(input_bytes(a.out/'rankings.jsonl'))!=meta['predictions_sha256'] or digest(input_bytes(a.out/'protocol.json'))!=meta['protocol_sha256']:
            raise ValueError('rankings/protocol changed')
        print(json.dumps(evaluate_orders(read_jsonl(a.out/'rankings.jsonl'),a.out,'bge_direct')['overall'],ensure_ascii=False,indent=2))
    else:globals()[a.command](a)


if __name__=='__main__':main()
