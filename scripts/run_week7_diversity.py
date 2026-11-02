"""Week7 hypothesis 1: frozen BGE scores + source/semantic redundancy ablation.

prepare -> embed --limit 5 -> embed -> rank -> evaluate.
Only embed calls the company endpoint. No torch, BGE or chat generation.
"""
import _bootstrap
import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import numpy as np
from civic_link.common import ROOT, digest, input_bytes, read_json, write_json, write_jsonl, read_jsonl
from evaluate_week7_bge_direct import direct_order
from week7_hypothesis_support import load_frozen, corpus, evaluate_orders, BASELINE, DB

INSTRUCTION = "Represent the user's input."
CONDITIONS = ('bge_direct','source_penalty','semantic_penalty','source_semantic_penalty')


def prepare(a):
    if a.out.exists(): raise ValueError('output exists; choose a new --out')
    queries,pools,protocol=load_frozen(); chunks,_=corpus(a.db,protocol)
    ids=sorted(set().union(*(set(s) for s in pools.values())))
    rows=[dict(chunk_id=c,document_id=chunks[c]['document_id'],
               text=chunks[c]['title']+'\n'+chunks[c]['text']) for c in ids]
    for r in rows:r['input_sha256']=digest(r['text'])
    a.out.mkdir(parents=True)
    write_jsonl(a.out/'embedding_inputs.jsonl',rows)
    write_json(a.out/'protocol.json',dict(hypothesis='Week7 semantic/source diversity H1 (not the previous query-decomposition H1)',
        baseline_sha256=digest(input_bytes(BASELINE)),queries_sha256=protocol['queries_sha256'],
        corpus_hash=protocol['corpus_hash'],inputs_sha256=digest(input_bytes(a.out/'embedding_inputs.jsonl')),
        query_count=len(queries),unique_chunks=len(ids),top_k=10,
        strength=a.strength,similarity_threshold=a.threshold,
        policy='minmax BGE relevance minus strength*source_seen minus strength*max(0,(max_cosine-threshold)/(1-threshold)); greedy top10; BGE tail',
        selection_uses_gold=False,independent_holdout=False))
    print(f'Prepared {len(queries)} questions / {len(ids)} unique chunks. No API calls.')


def inputs(out):
    protocol=read_json(out/'protocol.json')
    if digest(input_bytes(out/'embedding_inputs.jsonl'))!=protocol['inputs_sha256']:
        raise ValueError('embedding input changed')
    if digest(input_bytes(BASELINE))!=protocol['baseline_sha256']: raise ValueError('baseline changed')
    return protocol,read_jsonl(out/'embedding_inputs.jsonl')


def payload(model,text,fmt):
    if fmt=='qwen3-vl':
        return dict(model=model,encoding_format='float',messages=[
            dict(role='system',content=INSTRUCTION),
            dict(role='user',content=[dict(type='text',text=text)])])
    return dict(model=model,encoding_format='float',input=[text])


def decode_vector(response):
    rows=response.get('data',[])
    if len(rows)!=1 or rows[0].get('index',0)!=0: raise ValueError('expected one embedding with index 0')
    raw=rows[0].get('embedding')
    if not isinstance(raw,list) or not raw or any(type(v) not in (int,float) for v in raw):
        raise ValueError('embedding must be a nonempty numeric list')
    v=np.asarray(raw,dtype='<f4')
    if v.ndim!=1 or not np.isfinite(v).all() or np.linalg.norm(v.astype(float))==0:
        raise ValueError('invalid/zero embedding')
    return v


def cache_open(path,identity):
    c=sqlite3.connect(path)
    c.execute('create table if not exists metadata(key text primary key,value text)')
    c.execute('create table if not exists vectors(chunk_id text primary key,input_hash text,dimension integer,vector blob,checksum text,seconds real)')
    old=c.execute("select value from metadata where key='identity'").fetchone()
    if old and json.loads(old[0])!=identity:
        c.close();raise ValueError('embedding identity changed; use a new output directory')
    if not old:c.execute('insert into metadata values (?,?)',('identity',json.dumps(identity,sort_keys=True)))
    c.commit();return c


def read_cache(c, rows):
    expected={r['chunk_id']:r['input_sha256'] for r in rows};result={};dimension=None
    for cid,ih,dim,blob,checksum in c.execute('select chunk_id,input_hash,dimension,vector,checksum from vectors'):
        if cid not in expected or ih!=expected[cid] or hashlib.sha256(blob).hexdigest()!=checksum:
            raise ValueError('cached vector provenance/checksum differs')
        v=np.frombuffer(blob,dtype='<f4')
        if len(v)!=dim or not np.isfinite(v).all() or np.linalg.norm(v.astype(float))==0:
            raise ValueError('invalid cached vector')
        if dimension is not None and dim!=dimension:raise ValueError('mixed vector dimensions')
        dimension=dim;result[cid]=v
    return result


def embed(a):
    protocol,rows=inputs(a.out)
    endpoint=os.environ.get('CIVIC_EMBEDDING_URL','').rstrip('/')
    model=os.environ.get('CIVIC_EMBEDDING_MODEL','')
    parsed=urlsplit(endpoint)
    if parsed.scheme not in ('http','https') or not parsed.hostname or not model or parsed.query or parsed.fragment:
        raise ValueError('set CIVIC_EMBEDDING_URL to a base URL ending /v1 and CIVIC_EMBEDDING_MODEL')
    if not endpoint.endswith('/v1'):raise ValueError('CIVIC_EMBEDDING_URL must end /v1 (not /embeddings)')
    identity=dict(endpoint_sha256=digest(endpoint),model=model,format=a.format,
        instruction=INSTRUCTION if a.format=='qwen3-vl' else None,inputs_sha256=protocol['inputs_sha256'])
    path=a.out/'vectors.sqlite'
    with closing(cache_open(path,identity)) as cache:
        existing=read_cache(cache,rows)
        remaining=[r for r in rows if r['chunk_id'] not in existing]
        todo=remaining[:a.limit] if a.limit else remaining
        print(f'Cached {len(existing)}/{len(rows)}; requesting {len(todo)} unique chunks.',flush=True)
        headers={'Content-Type':'application/json'}
        if os.environ.get('CIVIC_EMBEDDING_API_KEY'):headers['Authorization']='Bearer '+os.environ['CIVIC_EMBEDDING_API_KEY']
        seconds=[];dim=len(next(iter(existing.values()))) if existing else None
        for i,r in enumerate(todo,1):
            started=time.perf_counter()
            req=urllib.request.Request(endpoint+'/embeddings',
                data=json.dumps(payload(model,r['text'],a.format),ensure_ascii=False).encode('utf-8'),headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=a.timeout) as response: obj=json.load(response)
            except urllib.error.HTTPError as error:
                raise RuntimeError(f'Embedding HTTP {error.code}; stopped without changing request format. Check server input format/model. Completed vectors are preserved.') from None
            v=decode_vector(obj)
            if dim is not None and len(v)!=dim:raise ValueError('server vector dimension changed')
            dim=len(v);elapsed=time.perf_counter()-started;seconds.append(elapsed);blob=v.tobytes()
            cache.execute('insert into vectors values (?,?,?,?,?,?)',
                          (r['chunk_id'],r['input_sha256'],dim,blob,hashlib.sha256(blob).hexdigest(),elapsed));cache.commit()
            if i<=5 or i%25==0 or i==len(todo):
                left=len(remaining)-i;eta=left*sum(seconds)/len(seconds)/60
                print(f'{len(existing)+i}/{len(rows)} cached; last {elapsed:.2f}s; approximate remaining {eta:.1f} min',flush=True)
        count=cache.execute('select count(*) from vectors').fetchone()[0]
        write_json(a.out/'embedding.run.json',dict(identity=identity,status='completed' if count==len(rows) else 'partial',
            cached=count,required=len(rows),dimension=dim,requests_this_invocation=len(seconds),
            seconds_this_invocation=sum(seconds),source_sha256=digest(Path(__file__).read_bytes()),
            note='No client truncation. Server model revision/truncation settings not verified; keep deployment fixed.'))
    print('Embedding cache ready.' if count==len(rows) else 'Probe/partial finished. Review time before running embed without --limit.')


def choose(scores,docids,vectors,condition,strength=.15,threshold=.8):
    base=direct_order(scores)
    if condition=='bge_direct':return base,[]
    if condition not in CONDITIONS:raise ValueError('unknown condition')
    values=np.array([scores[c] for c in base],dtype=float);span=float(values.max()-values.min())
    relevance=(values-values.min())/span if span else np.ones(len(base))
    semantic=condition in ('semantic_penalty','source_semantic_penalty')
    source=condition in ('source_penalty','source_semantic_penalty')
    matrix=None
    if semantic:
        matrix=np.stack([vectors[c] for c in base]).astype(np.float64)
        norms=np.linalg.norm(matrix,axis=1)
        if not np.isfinite(matrix).all() or np.any(norms==0):raise ValueError('invalid vectors')
        matrix/=norms[:,None]
    selected=[];seen=set();trace=[];maximum=np.zeros(len(base));remaining=set(range(len(base)))
    for step in range(min(10,len(base))):
        redundancy=np.maximum(0,(maximum-threshold)/(1-threshold)) if semantic else np.zeros(len(base))
        source_values=np.array([float(docids[c] in seen) for c in base]) if source else np.zeros(len(base))
        utility=relevance-strength*redundancy-strength*source_values
        best=min(remaining,key=lambda i:(-float(utility[i]),-scores[base[i]],base[i]))
        cid=base[best];selected.append(cid);remaining.remove(best)
        trace.append(dict(rank=step+1,chunk_id=cid,bge_rank=best+1,bge_raw_score=scores[cid],
            normalized_bge=float(relevance[best]),max_selected_cosine=float(maximum[best]),
            semantic_penalty=float(strength*redundancy[best]),source_penalty=float(strength*source_values[best]),
            selection_score=float(utility[best]),document_id=docids[cid]))
        seen.add(docids[cid])
        if semantic:maximum=np.maximum(maximum,np.clip(matrix@matrix[best],-1,1))
    return selected+[c for c in base if c not in set(selected)],trace


def rank(a):
    protocol,rows=inputs(a.out)
    if (a.out/'rankings.jsonl').exists():raise ValueError('rankings already exist; use a new experiment for changed settings')
    queries,pools,_=load_frozen();meta=read_json(a.out/'embedding.run.json')
    if meta['status']!='completed' or meta['identity']['inputs_sha256']!=protocol['inputs_sha256']:
        raise ValueError('complete embedding cache required')
    with closing(sqlite3.connect((a.out/'vectors.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as c:
        saved=json.loads(c.execute("select value from metadata where key='identity'").fetchone()[0])
        if saved!=meta['identity']:raise ValueError('cache/run identity differs')
        vectors=read_cache(c,rows)
    if set(vectors)!={r['chunk_id'] for r in rows}:raise ValueError('missing embedding vectors')
    docs={r['chunk_id']:r['document_id'] for r in rows};results=[];start=time.perf_counter()
    for i,qid in enumerate(queries,1):
        orders={};traces={};stats={}
        for condition in CONDITIONS:
            order,trace=choose(pools[qid],docs,vectors,condition,protocol['strength'],protocol['similarity_threshold'])
            orders[condition]=order;traces[condition]=trace
            selected=[docs[c] for c in order[:10]]
            stats[condition]=dict(unique_sources=len(set(selected)),max_source_count=max(selected.count(d) for d in selected))
        results.append(dict(query_id=qid,orders=orders,selection_trace=traces,source_stats=stats))
        if i%25==0 or i==len(queries):print(f'Ranked {i}/{len(queries)}; elapsed {time.perf_counter()-start:.1f}s',flush=True)
    write_jsonl(a.out/'rankings.jsonl',results)
    write_json(a.out/'rank.run.json',dict(status='completed',query_count=len(results),api_calls=0,
        source_sha256=digest(Path(__file__).read_bytes()),protocol_sha256=digest(input_bytes(a.out/'protocol.json')),
        baseline_sha256=protocol['baseline_sha256'],vectors_sha256=digest((a.out/'vectors.sqlite').read_bytes()),
        predictions_sha256=digest(input_bytes(a.out/'rankings.jsonl')),seconds=time.perf_counter()-start))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'week7/results/ranking_cases_hypotheses/h1_diversity')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('prepare');s.add_argument('--db',type=Path,default=DB)
    s.add_argument('--strength',type=float,default=.15);s.add_argument('--threshold',type=float,default=.8)
    s=sub.add_parser('embed');s.add_argument('--limit',type=int);s.add_argument('--timeout',type=int,default=120)
    s.add_argument('--format',choices=['qwen3-vl','text'],default='qwen3-vl')
    sub.add_parser('rank');sub.add_parser('evaluate');a=p.parse_args()
    if a.command=='prepare' and (not math.isfinite(a.strength) or not 0<=a.strength<=1 or not 0<=a.threshold<1):raise ValueError('invalid penalty settings')
    if a.command=='embed' and ((a.limit is not None and a.limit<1) or a.timeout<1):raise ValueError('invalid limit/timeout')
    if a.command=='evaluate':
        protocol,_=inputs(a.out);meta=read_json(a.out/'rank.run.json')
        if meta['status']!='completed' or digest(input_bytes(a.out/'rankings.jsonl'))!=meta['predictions_sha256'] or digest(input_bytes(a.out/'protocol.json'))!=meta['protocol_sha256']:
            raise ValueError('rankings/protocol changed')
        result=evaluate_orders(read_jsonl(a.out/'rankings.jsonl'),a.out,'bge_direct')
        print(json.dumps(result['overall'],ensure_ascii=False,indent=2))
    else:globals()[a.command](a)


if __name__=='__main__':main()
