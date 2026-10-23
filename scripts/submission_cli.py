import argparse,json,re
from pathlib import Path
from civic_data import ROOT,connect,read_jsonl
from release_support import local_path,require
from release_verify import preflight,restore,verify

def decode_raw(data):
    declared=re.search(rb'charset\s*=\s*[\"\x27]?([A-Za-z0-9_-]+)',data[:10000],re.I)
    encodings=[declared.group(1).decode('ascii')] if declared else []
    for encoding in dict.fromkeys(encodings+['utf-8','cp949','euc-kr']):
        try:return dict(text=data.decode(encoding),encoding=encoding)
        except (UnicodeError,LookupError):continue
    return dict(text=data.decode('utf-8',errors='replace'),encoding='utf-8')

def inspect(root,command,identifier=None):
    root=Path(root).resolve();m=preflight(root,json.loads(local_path(root,'submission_manifest.json').read_text()))
    qs=read_jsonl(root/'questions.jsonl');services=read_jsonl(root/'data/services.jsonl')
    if command=='questions':return qs
    if command=='services':return services
    if command=='inventory':return dict(counts=m['counts'],files=m['files'])
    if command=='question':
        q=next((q for q in qs if q['question_id']==identifier),None);require(q is not None,'unknown question ID');return q
    if command=='family':
        groups=read_jsonl(root/'data/intent_groups.jsonl')
        group=next((g for g in groups if identifier==g['split_group_id'] or identifier in g['intent_family_ids'] or identifier in g['question_ids']),None)
        require(group is not None,'unknown family ID');return dict(group=group,questions=[q for q in qs if q['question_id'] in group['question_ids']])
    if command=='service':
        service=next((s for s in services if s['service_id']==identifier),None);require(service is not None,'unknown service ID')
        graph=json.loads((root/'data/processed/graph.json').read_text())
        return dict(service=service,questions=[q for q in qs if identifier in q['service_ids']],relations=[e for e in graph['edges'] if identifier in (e['source'],e['target'])])
    db=restore(root);ev=json.loads((root/'data/processed/graph.evidence.json').read_text())
    if command=='evidence':
        require(identifier in ev['records'],'unknown evidence ID');return dict(evidence_id=identifier,record=ev['records'][identifier])
    with connect(db,True) as c:
        if command=='documents':return [dict(r) for r in c.execute('SELECT document_id,alias,title,source_type,source_url FROM documents ORDER BY document_id')]
        if command in ('document','raw'):
            r=c.execute('SELECT * FROM documents WHERE document_id=? OR alias=?',(identifier,identifier)).fetchone();require(r is not None,'unknown document ID')
            if command=='raw':return dict(path=r['raw_path'],**decode_raw(local_path(root,r['raw_path']).read_bytes()))
            return dict(document=dict(r),chunks=[dict(x) for x in c.execute('SELECT chunk_id,locator,start_char,end_char FROM chunks WHERE document_id=? ORDER BY ordinal',(r['document_id'],))])
        if command=='chunk':
            r=c.execute('SELECT * FROM chunks WHERE chunk_id=?',(identifier,)).fetchone();require(r is not None,'unknown chunk ID');return dict(chunk=dict(r),evidence={k:v for k,v in ev['records'].items() if identifier in v['chunk_ids']})
    raise ValueError('unknown command')

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    for name in ('inventory','questions','services','documents','restore','verify'):sub.add_parser(name)
    for name in ('question','family','service','document','raw','chunk','evidence'):sub.add_parser(name).add_argument('identifier')
    search=sub.add_parser('search');search.add_argument('question');search.add_argument('--hops',type=int,choices=range(4),default=2)
    a=p.parse_args()
    if a.command=='verify':result=verify(ROOT)
    elif a.command=='restore':result=dict(database=str(restore(ROOT).relative_to(ROOT)))
    elif a.command=='search':
        from graph_construct import load_graph
        from graph_retrieval import GraphRetriever
        db=restore(ROOT);g,ev=load_graph(ROOT/'data/processed/graph.json',db)
        result=GraphRetriever(g,db,ev).search(a.question,max_hops=a.hops)
    else:result=inspect(ROOT,a.command,getattr(a,'identifier',None))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
