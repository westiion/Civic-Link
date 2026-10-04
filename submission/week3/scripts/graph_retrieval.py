
from __future__ import annotations
import argparse
import json
import re
import time
from collections import deque
from pathlib import Path
from civic_data import ROOT,connect,digest,read_jsonl,write_json,write_jsonl
from graph_construct import compact,load_graph

DEFAULTS=dict(top_k=10,seed_k=3,max_hops=2,max_nodes=12000,seed_threshold=.18)


def bigrams(value):
    s=compact(value)
    return {s[i:i+2] for i in range(len(s)-1)}


def label_score(question,label):
    q=compact(question);n=compact(label)
    if not q or not n:return 0.
    return .7*len(bigrams(q)&bigrams(n))/max(1,len(bigrams(n)))+(.3 if n in q else 0.)


def path_text(graph,nodes,edges):
    text=graph.nodes[nodes[0]]['type']
    for edge in edges:text+='-'+graph.edges[edge['source'],edge['target'],edge['key']]['relation']+'-'+graph.nodes[edge['target']]['type']
    return text


class GraphRetriever:
    def __init__(self,graph,db,evidence):
        self.graph=graph;self.evidence=evidence
        with connect(db,True) as c:
            self.chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
            self.docs={r['document_id']:dict(r) for r in c.execute('SELECT document_id,source_url FROM documents')}
        self.chunk_terms={cid:bigrams(ch['text']) for cid,ch in self.chunks.items()}
        self.outgoing={n:sorted([(v,k,e) for _,v,k,e in graph.out_edges(n,keys=True,data=True)],key=lambda x:(x[0],x[1])) for n in graph}

    def seeds(self,question,seed_k=3,threshold=.18):
        candidates=[]
        for nid,n in self.graph.nodes(data=True):
            if n['type']=='Service':score=max(label_score(question,a) for a in n['aliases'])
            elif n['type']=='Law' and compact(n['label']) in compact(question):score=1.
            else:continue
            if score>=threshold:candidates.append(dict(node_id=nid,label=n['label'],score=score))
        return sorted(candidates,key=lambda x:(-x['score'],x['node_id']))[:seed_k]

    def search(self,question,query_id='interactive',**options):
        cfg={**DEFAULTS,**options}
        if set(cfg)!=set(DEFAULTS):raise ValueError('unknown search option')
        if any(type(cfg[k]) is not int for k in ['top_k','seed_k','max_hops','max_nodes']):raise ValueError('integer limits required')
        if min(cfg['top_k'],cfg['seed_k'],cfg['max_nodes'])<1 or cfg['max_hops']<0 or not 0<cfg['seed_threshold']<=1:raise ValueError('invalid search limits')
        if not isinstance(question,str) or not question.strip():raise ValueError('empty question')
        seeds=self.seeds(question,cfg['seed_k'],cfg['seed_threshold']);candidates={};reached=set();truncated=False
        qterms=bigrams(question);method='graph_h'+str(cfg['max_hops'])
        def collect(kind,owner,nodes,edges,seed):
            for eid in self.evidence[kind+'_bindings'].get(owner,[]):
                ev=self.evidence['records'][eid]
                if kind=='node' and edges and self.graph.nodes[owner]['type'] in ('Document','Agency'):
                    source_docs={self.evidence['records'][x]['source_document_id'] for x in self.evidence['edge_bindings'][edges[-1]['key']]}
                    if ev['source_document_id'] not in source_docs:continue
                for cid in ev['chunk_ids']:

                    relevance=len(qterms & self.chunk_terms[cid])/max(1,len(qterms))
                    score=seed['score']*(.45+relevance)/(1+.12*len(edges))
                    if ev['role']=='condition':score*=.95
                    identity=(tuple(nodes),tuple(e['key'] for e in edges),kind,owner,eid)
                    old=candidates.get(cid)
                    if old and (score<old['score'] or (score==old['score'] and identity>=old['_identity'])):continue
                    p=path_text(self.graph,nodes,edges)
                    bound=self.graph.nodes[owner]['type']+' node evidence' if kind=='node' else self.graph.edges[edges[-1]['source'],edges[-1]['target'],owner]['relation']+' edge evidence'
                    candidates[cid]=dict(score=score,_identity=identity,seed_id=seed['node_id'],path_nodes=list(nodes),path_edges=list(edges),
                        path=p,evidence_path=p+' → '+bound,graph_hops=len(edges),
                        document_hops=sum(self.graph.edges[e['source'],e['target'],e['key']]['document_hop'] for e in edges),
                        evidence_binding=dict(kind=kind,owner_id=owner,evidence_id=eid),
                        evidence_quote=ev['quote'],evidence_span=[ev['start_char'],ev['end_char']],
                        evidence_role=ev['role'],evidence_source_document_id=ev['source_document_id'])
        for seed in seeds:
            root=seed['node_id'];queue=deque([(root,[root],[])]);seen={root}
            while queue:
                if len(reached)>=cfg['max_nodes']:
                    truncated=True;break
                u,nodes,edges=queue.popleft();reached.add(u)
                collect('node',u,nodes,edges,seed)
                if len(edges)>=cfg['max_hops']:continue
                for v,key,e in self.outgoing[u]:
                    next_edges=edges+[dict(source=u,target=v,key=key,relation=e['relation'])]
                    next_nodes=nodes+[v]

                    collect('edge',key,next_nodes,next_edges,seed)
                    if v not in seen:seen.add(v);queue.append((v,next_nodes,next_edges))
        hits=[]
        for rank,(cid,info) in enumerate(sorted(candidates.items(),key=lambda x:(-x[1]['score'],x[0]))[:cfg['top_k']],1):
            ch=self.chunks[cid]
            hit={k:v for k,v in info.items() if k!='_identity'}
            hit.update(query_id=query_id,retriever=method,rank=rank,chunk_id=cid,document_id=ch['document_id'],
                       title=ch['title'],snippet=ch['text'][:300],source_url=self.docs[ch['document_id']]['source_url'],
                       evidence_status='retrieved_not_entailment_verified')
            hits.append(hit)
        return dict(query_id=query_id,retriever=method,hits=hits,seeds=seeds,candidate_chunk_ids=sorted(candidates),
                    candidate_chunk_count=len(candidates),visited_node_count=len(reached),traversal_truncated=truncated,
                    status='retrieved' if hits else 'no_bound_evidence' if seeds else 'no_linked_entity')

    def run(self,queries,**cfg):
        rows=[];times=[];seen=set()
        for q in queries:
            if set(q)!={'query_id','question'}:raise ValueError('only query_id/question accepted; no gold fields')
            qid=q['query_id']
            if not isinstance(qid,str) or not qid or qid in seen:raise ValueError('invalid/duplicate query ID')
            seen.add(qid);start=time.perf_counter();rows.append(self.search(q['question'],qid,**cfg));times.append((time.perf_counter()-start)*1000)
        return rows,times


def main():
    p=argparse.ArgumentParser(description='Graph retrieval.')
    p.add_argument('--db',type=Path,default=ROOT/'data/processed/civic.sqlite');p.add_argument('--graph',type=Path,default=ROOT/'data/processed/graph.json')
    inputs=p.add_mutually_exclusive_group(required=True);inputs.add_argument('--query');inputs.add_argument('--queries',type=Path)
    p.add_argument('--output',type=Path)
    for key in ['top_k','seed_k','max_hops','max_nodes']:p.add_argument('--'+key.replace('_','-'),type=int,default=DEFAULTS[key])
    a=p.parse_args();g,ev=load_graph(a.graph,a.db);r=GraphRetriever(g,a.db,ev)
    qs=read_jsonl(a.queries) if a.queries else [dict(query_id='interactive',question=a.query)]
    cfg={k:getattr(a,k) for k in ['top_k','seed_k','max_hops','max_nodes']};rows,times=r.run(qs,**cfg)
    if a.output:
        write_jsonl(a.output,rows);write_json(a.output.with_suffix('.run.json'),dict(config=cfg,query_latency_ms=times,graph_sha256=digest(a.graph.read_bytes()),evidence_sha256=digest(a.graph.with_suffix('.evidence.json').read_bytes()),generation=None))
        print(f'{len(rows)} query results written')
    else:print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
