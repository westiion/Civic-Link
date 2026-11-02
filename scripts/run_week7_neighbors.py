"""H4 same-article neighbors within frozen candidates; no API or BGE inference."""
import _bootstrap
import argparse
import json
import re
import time
from pathlib import Path
from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from evaluate_week7_bge_direct import direct_order
from week7_hypothesis_support import load_frozen, corpus, evaluate_orders, DB, BASELINE, SOURCE


def build_index(chunks, graph):
    allowed={(n.get('source_document_id'),n.get('locator')):n['id'] for n in graph['nodes'] if n['type']=='Article'}
    groups={}
    for cid,c in chunks.items():
        key=(c['document_id'],c['locator'])
        if key not in allowed or not re.fullmatch(r'제\d+조(?:의\d+)?',c['locator']):continue
        groups.setdefault(key,[]).append(c)
    index={}
    for key,items in groups.items():
        for c in items:
            index[c['chunk_id']]=[dict(chunk_id=t['chunk_id'],article_id=allowed[key],document_id=key[0],locator=key[1],
                anchor_ordinal=c['ordinal'],target_ordinal=t['ordinal'],distance=abs(c['ordinal']-t['ordinal']),
                anchor_span=[c['start_char'],c['end_char']],target_span=[t['start_char'],t['end_char']])
                for t in items if t['chunk_id']!=c['chunk_id']]
    return index


def select(scores,index,slots=2,adjacent=True):
    if not 0<=slots<=5:raise ValueError('slots must be 0..5')
    base=direct_order(scores);positions={c:i+1 for i,c in enumerate(base)}
    if slots==0:return base,dict(eligible=[],selected=[])
    protected=base[:10-slots];eligible=[]
    for anchor in protected:
        for target in index.get(anchor,[]):
            cid=target['chunk_id']
            if cid not in scores or cid in protected or (adjacent and target['distance']!=1):continue
            eligible.append(dict(**target,anchor_chunk=anchor,anchor_rank=positions[anchor],
                                 bge_rank=positions[cid],bge_score=scores[cid]))
    eligible.sort(key=lambda r:(-r['bge_score'],r['chunk_id'],r['anchor_rank'],r['article_id']))
    selected=[];seen=set(protected)
    for row in eligible:
        if row['chunk_id'] in seen:continue
        selected.append(row);seen.add(row['chunk_id'])
        if len(selected)==slots:break
    head=protected+[r['chunk_id'] for r in selected]
    return head+[c for c in base if c not in set(head)],dict(eligible=eligible,selected=selected)


def prepare(a):
    if a.out.exists():raise ValueError('output exists; use new --out')
    queries,pools,origin=load_frozen();chunks,_=corpus(a.db,origin)
    gp=ROOT/'data/graph/graph.json';source=read_json(SOURCE/'h3.run.json')
    if digest(input_bytes(gp))!=source['graph_sha256']:raise ValueError('graph differs from baseline')
    index=build_index(chunks,read_json(gp));a.out.mkdir(parents=True)
    write_json(a.out/'neighbor_index.json',index)
    write_json(a.out/'protocol.json',dict(hypothesis='H4 same article neighboring chunks',
        baseline_sha256=digest(input_bytes(BASELINE)),queries_sha256=origin['queries_sha256'],corpus_hash=origin['corpus_hash'],
        graph_sha256=source['graph_sha256'],index_sha256=digest(input_bytes(a.out/'neighbor_index.json')),
        slots=a.slots,top_k=10,selection_uses_gold=False,independent_holdout=False,
        policy='protect BGE top(10-slots); same document and exact graph Article locator; adjacency means absolute document ordinal difference 1; select by BGE; no recursive expansion; preserve full pool'))
    reachable=sum(any(t['chunk_id'] in pool and t['distance']==1 for c in direct_order(pool)[:10-a.slots] for t in index.get(c,[])
                      if t['chunk_id'] not in direct_order(pool)[:10-a.slots]) for pool in pools.values()) if a.slots else 0
    print(f'Prepared {len(queries)} questions; {reachable} have eligible adjacent candidates. No API calls.')


def check(out):
    p=read_json(out/'protocol.json');_,_,origin=load_frozen()
    if p['queries_sha256']!=origin['queries_sha256'] or p['baseline_sha256']!=digest(input_bytes(BASELINE)):raise ValueError('frozen inputs changed')
    if p['index_sha256']!=digest(input_bytes(out/'neighbor_index.json')):raise ValueError('index changed')
    return p


def rank(a):
    p=check(a.out)
    if (a.out/'rankings.jsonl').exists():raise ValueError('rankings already exist')
    queries,pools,_=load_frozen();index=read_json(a.out/'neighbor_index.json');results=[];start=time.perf_counter()
    for qid in queries:
        scores=pools[qid];orders={'bge_direct':direct_order(scores)};trace={}
        for name,adjacent in [('same_article_control',False),('adjacent_article',True)]:
            orders[name],trace[name]=select(scores,index,p['slots'],adjacent)
        results.append(dict(query_id=qid,orders=orders,neighbor_trace=trace))
    write_jsonl(a.out/'rankings.jsonl',results)
    write_json(a.out/'rank.run.json',dict(status='completed',query_count=len(results),api_calls=0,
        source_sha256=digest(Path(__file__).read_bytes()),protocol_sha256=digest(input_bytes(a.out/'protocol.json')),
        predictions_sha256=digest(input_bytes(a.out/'rankings.jsonl')),seconds=time.perf_counter()-start))
    print(f'Ranked {len(results)} questions. No API or model inference.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'week7/results/ranking_cases_hypotheses/h4_neighbors')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('prepare');s.add_argument('--slots',type=int,choices=range(6),default=2);s.add_argument('--db',type=Path,default=DB)
    sub.add_parser('rank');sub.add_parser('evaluate');a=p.parse_args()
    if a.command=='evaluate':
        check(a.out);meta=read_json(a.out/'rank.run.json')
        for f,k in [('rankings.jsonl','predictions_sha256'),('protocol.json','protocol_sha256')]:
            if digest(input_bytes(a.out/f))!=meta[k]:raise ValueError('changed '+f)
        print(json.dumps(evaluate_orders(read_jsonl(a.out/'rankings.jsonl'),a.out,'bge_direct')['overall'],ensure_ascii=False,indent=2))
    else:globals()[a.command](a)


if __name__=='__main__':main()
