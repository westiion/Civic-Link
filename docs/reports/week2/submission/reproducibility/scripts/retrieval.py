from __future__ import annotations
import argparse,ast,json,math,os,time,urllib.request,urllib.error
from collections import Counter,defaultdict
from pathlib import Path
from civic_data import connect,digest,now,read_jsonl,write_json,write_jsonl
from tokenization import tokenize
try:
    import numpy as np
except ImportError:
    np=None

class BM25:
    def __init__(self,docs,k1=1.2,b=.75):
        if k1<=0 or not 0<=b<=1:raise ValueError('invalid BM25 parameters')
        if not docs:raise ValueError('empty corpus')
        self.docs=docs;self.k1=k1;self.b=b;self.postings=defaultdict(list);self.lengths=[]
        if len({d['chunk_id'] for d in docs})!=len(docs):raise ValueError('duplicate chunk ID')
        for i,d in enumerate(docs):
            counts=Counter(tokenize(d['title']+' '+d['text']));self.lengths.append(sum(counts.values()))
            for term,tf in counts.items():self.postings[term].append((i,tf))
        self.avg=sum(self.lengths)/len(docs)
        if not self.avg:raise ValueError('empty searchable text')
        self.idf={t:math.log(1+(len(docs)-len(p)+.5)/(len(p)+.5)) for t,p in self.postings.items()}
    def search(self,text,k=10):
        if k<1:raise ValueError('k must be positive')
        scores=defaultdict(float)
        for t in sorted(set(tokenize(text))):
            for i,tf in self.postings.get(t,[]):
                norm=self.k1*(1-self.b+self.b*self.lengths[i]/self.avg)
                scores[i]+=self.idf[t]*tf*(self.k1+1)/(tf+norm)
        return [(self.docs[i]['chunk_id'],v) for i,v in sorted(scores.items(),key=lambda x:(-x[1],self.docs[x[0]]['chunk_id']))[:k]]

def normalize_vector(vector):
    if not isinstance(vector,list) or not vector or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):raise ValueError('embedding must be a nonempty finite numeric vector')
    norm=math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(norm) or norm==0:raise ValueError('zero or overflowing embedding')
    return [float(x)/norm for x in vector]

class EmbeddingClient:
    def __init__(self,base_url,model,api_key=None,query_prefix='',document_prefix='',timeout=60,input_format='plain'):
        if not base_url or not model:raise ValueError('embedding URL and model are required')
        if input_format not in ('plain','qwen3-vl'):raise ValueError('unknown embedding input format')
        self.url=base_url.rstrip('/')+'/embeddings';self.model=model;self.key=api_key
        self.query_prefix=query_prefix;self.document_prefix=document_prefix;self.timeout=timeout
        self.input_format=input_format
        config={'url':self.url,'model':model,'query_prefix':query_prefix,'document_prefix':document_prefix}
        if input_format!='plain':config['input_format']=input_format;config['template_version']=1
        self.fingerprint=digest(json.dumps(config,sort_keys=True))
    def format_input(self,text,kind='document'):
        if kind not in ('document','query'):raise ValueError('unknown embedding input kind')
        text=(self.query_prefix if kind=='query' else self.document_prefix)+text
        if self.input_format=='qwen3-vl':

            return "<|im_start|>system\nRepresent the user's input.<|im_end|>\n<|im_start|>user\n"+text+"<|im_end|>\n<|im_start|>assistant\n"
        return text
    def encode(self,texts,kind='document'):
        if not texts:return []
        body={'model':self.model,'input':[self.format_input(t,kind) for t in texts]}
        if self.input_format=='qwen3-vl':body.update(add_special_tokens=False,encoding_format='float')
        payload=json.dumps(body).encode()
        headers={'Content-Type':'application/json'}
        if self.key:headers['Authorization']='Bearer '+self.key
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.url,data=payload,headers=headers),timeout=self.timeout) as r:data=json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429,500,502,503,504) and attempt<2:time.sleep(2**attempt);continue
                raise RuntimeError(f'embedding endpoint HTTP {e.code}; no response body or credentials logged') from None
            except (urllib.error.URLError,TimeoutError):
                if attempt<2:time.sleep(2**attempt);continue
                raise RuntimeError('embedding endpoint connection failed') from None
        rows=data.get('data')
        if not isinstance(rows,list) or len(rows)!=len(texts):raise ValueError('embedding response count mismatch')
        if sorted(r.get('index',-1) for r in rows)!=list(range(len(texts))):raise ValueError('invalid embedding response indices')
        vectors=[normalize_vector(r.get('embedding')) for r in sorted(rows,key=lambda x:x['index'])]
        if len({len(v) for v in vectors})!=1:raise ValueError('mixed embedding dimensions')
        return vectors

def client_example(path):

    tree=ast.parse(Path(path).read_text())
    for n in ast.walk(tree):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='OpenAI':
            return {k.arg:ast.literal_eval(k.value) for k in n.keywords if k.arg in ('base_url','api_key')}
    raise ValueError('OpenAI literal configuration not found')

def load_chunks(c):return [dict(r) for r in c.execute('SELECT ch.*,d.source_url FROM chunks ch JOIN documents d USING(document_id) ORDER BY ch.chunk_id')]

def index_dense(db,client,batch_size=16,progress=None):
    if batch_size<1:raise ValueError('invalid batch size')
    with connect(db) as c:
        docs=load_chunks(c);stored={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM embeddings WHERE model=? AND encoder_fingerprint=?',(client.model,client.fingerprint))}
        pending=[];dim=None
        for d in docs:
            text=d['title']+'\n'+d['text'];input_hash=digest(client.document_prefix+text);old=stored.get(d['chunk_id'])
            if old and old['input_hash']==input_hash:
                if dim is not None and dim!=old['dimension']:raise ValueError('mixed cache dimensions')
                dim=old['dimension'];continue
            pending.append((d,text,input_hash))
        for start in range(0,len(pending),batch_size):
            batch=pending[start:start+batch_size];vectors=client.encode([x[1] for x in batch])
            for (d,text,input_hash),v in zip(batch,vectors):
                if dim is not None and len(v)!=dim:raise ValueError('model dimension changed; use new model/revision identity')
                dim=len(v)
                c.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?,?,?,?)',(d['chunk_id'],client.model,client.fingerprint,dim,json.dumps(v),input_hash,now()))
            c.commit()
            if progress:progress({'embedded':min(start+batch_size,len(pending)),'pending_total':len(pending),'dimension':dim})
        return {'model':client.model,'dimension':dim,'chunks':len(docs),'newly_embedded':len(pending),'encoder_fingerprint':client.fingerprint}

class Dense:
    def __init__(self,c,docs,client):
        self.docs=docs;self.client=client;self.vectors=[];self.dim=None
        stored={r['chunk_id']:r for r in c.execute('SELECT * FROM embeddings WHERE model=? AND encoder_fingerprint=?',(client.model,client.fingerprint))}
        for d in docs:
            r=stored.get(d['chunk_id'])
            if r is None or r['input_hash']!=digest(client.document_prefix+d['title']+'\n'+d['text']):raise ValueError('missing or stale embeddings; run embed first')
            v=normalize_vector(json.loads(r['vector_json']))
            if len(v)!=r['dimension']:raise ValueError('stored dimension disagrees with vector')
            if self.dim is not None and self.dim!=len(v):raise ValueError('mixed dimensions')
            self.dim=len(v);self.vectors.append(v)
        self.matrix=np.asarray(self.vectors,dtype=np.float64) if np is not None else None
        if self.matrix is not None:self.vectors=[]
        self.backend='numpy_float64_exact' if self.matrix is not None else 'python_exact'
    def search(self,text,k=10):
        if k<1:raise ValueError('k must be positive')
        q=self.client.encode([text],'query')[0]
        if len(q)!=self.dim:raise ValueError('query/document dimension mismatch')
        scores=(self.matrix @ np.asarray(q,dtype=np.float64)).tolist() if self.matrix is not None else [sum(a*b for a,b in zip(q,v)) for v in self.vectors]
        ranked=[(d['chunk_id'],s) for d,s in zip(self.docs,scores)]
        return sorted(ranked,key=lambda x:(-x[1],x[0]))[:k]

def rrf(rankings,k=10,constant=60):
    if k<1 or constant<0:raise ValueError('invalid RRF parameters')
    scores=defaultdict(float)
    for ranking in rankings:
        ids=[x[0] for x in ranking]
        if len(ids)!=len(set(ids)):raise ValueError('duplicate ID in ranking')
        for rank,cid in enumerate(ids,1):scores[cid]+=1/(constant+rank)
    return sorted(scores.items(),key=lambda x:(-x[1],x[0]))[:k]

def run(db,queries,method='bm25',top_k=10,candidate_k=100,client=None):
    if top_k<1 or candidate_k<top_k:raise ValueError('require candidate_k >= top_k >= 1')
    seen=set()
    for q in queries:
        if set(q)!={'query_id','question'} or not isinstance(q['question'],str) or not q['question'].strip():raise ValueError('input must contain only nonempty query_id/question')
        if not isinstance(q['query_id'],str) or not q['query_id'] or q['query_id'] in seen:raise ValueError('invalid or duplicate query ID')
        seen.add(q['query_id'])
    with connect(db,True) as c:
        docs=load_chunks(c);lookup={d['chunk_id']:d for d in docs};bm=BM25(docs) if method in ('bm25','hybrid') else None
        dense=Dense(c,docs,client) if method in ('dense','hybrid') and client else None
        if method in ('dense','hybrid') and dense is None:raise ValueError('real embedding client required; no mock fallback')
        results=[];timings=[]
        for q in queries:
            start=time.perf_counter()
            if method=='bm25':ranked=bm.search(q['question'],top_k)
            elif method=='dense':ranked=dense.search(q['question'],top_k)
            elif method=='hybrid':ranked=rrf([bm.search(q['question'],candidate_k),dense.search(q['question'],candidate_k)],top_k)
            else:raise ValueError('unknown method')
            timings.append((time.perf_counter()-start)*1000)
            hits=[]
            for rank,(cid,score) in enumerate(ranked,1):
                d=lookup[cid];hits.append({'query_id':q['query_id'],'retriever':method,'rank':rank,'document_id':d['document_id'],'chunk_id':cid,'score':score,'title':d['title'],'snippet':d['text'][:300],'source_url':d['source_url']})
            results.append({'query_id':q['query_id'],'retriever':method,'hits':hits})
        meta={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM metadata')}
    settings={'method':method,'query_count':len(queries),'corpus_hash':meta['corpus_hash'],'top_k':top_k,'candidate_k':candidate_k,'bm25':{'k1':1.2,'b':.75,'tokenizer':'words+Hangul bigrams'},'rrf_constant':60,'embedding_model':client.model if client else None,'encoder_fingerprint':client.fingerprint if client else None,'created_at':now(),'query_latency_ms':timings,'generation':None,'answerability_classifier':None}
    if dense:settings.update(dense_backend=dense.backend,embedding_input_format=client.input_format,embedding_dimension=dense.dim,query_prefix=client.query_prefix,document_prefix=client.document_prefix)
    return results,settings

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['search','embed']);p.add_argument('--db',type=Path,required=True)
    p.add_argument('--queries',type=Path);p.add_argument('--query');p.add_argument('--method',choices=['bm25','dense','hybrid'],default='bm25');p.add_argument('--output',type=Path)
    p.add_argument('--top-k',type=int,default=10);p.add_argument('--candidate-k',type=int,default=100);p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--embedding-url',default=os.environ.get('CIVIC_EMBEDDING_URL'));p.add_argument('--embedding-model',default=os.environ.get('CIVIC_EMBEDDING_MODEL'));p.add_argument('--client-example',type=Path)
    p.add_argument('--embedding-format',choices=['plain','qwen3-vl'],default='plain')
    p.add_argument('--query-prefix',default='');p.add_argument('--document-prefix',default='');a=p.parse_args();client=None
    if a.command=='embed' or a.method in ('dense','hybrid'):
        cfg=client_example(a.client_example) if a.client_example else {}
        client=EmbeddingClient(a.embedding_url or cfg.get('base_url'),a.embedding_model,os.environ.get('CIVIC_EMBEDDING_API_KEY') or cfg.get('api_key'),a.query_prefix,a.document_prefix,input_format=a.embedding_format)
    if a.command=='embed':
        def progress(value):print(json.dumps(value),flush=True)
        print(json.dumps(index_dense(a.db,client,a.batch_size,progress)));return
    if bool(a.query)==bool(a.queries):p.error('choose exactly one of --query or --queries')
    queries=read_jsonl(a.queries) if a.queries else [{'query_id':'interactive','question':a.query}]
    results,settings=run(a.db,queries,a.method,a.top_k,a.candidate_k,client)
    settings['queries_sha256']=digest(a.queries.read_bytes()) if a.queries else digest(a.query)
    if a.output:
        write_jsonl(a.output,results);settings['predictions_sha256']=digest(a.output.read_bytes());write_json(a.output.with_suffix('.run.json'),settings)
        print(f'{len(results)} queries written to {a.output}')
    else:print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
