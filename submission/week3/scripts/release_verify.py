import ast,collections,csv,hashlib,io,json,math,os,re,shutil,sqlite3,tempfile,tokenize,zipfile
from pathlib import Path
from urllib.parse import unquote
from civic_data import ROOT,connect,read_jsonl,digest
from graph_construct import load_graph,validate_graph
from graph_retrieval import path_text
from release_support import local_path,require

def sha(path):
    if hasattr(path,'read'):
        position=path.tell();path.seek(0);result=hashlib.file_digest(path,'sha256').hexdigest();path.seek(position);return result
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def restore(root):
    root=Path(root).resolve();asset=json.loads(local_path(root,'submission_manifest.json').read_text())['corpus_asset']
    if 'parts' not in asset:return _restore(root,local_path(root,asset['path']))
    require(bool(asset['parts']),'empty corpus archive parts')
    require(len({p['path'] for p in asset['parts']})==len(asset['parts']),'duplicate corpus archive parts')
    with tempfile.TemporaryFile() as archive:
        for part in asset['parts']:
            path=local_path(root,part['path'])
            require(path.stat().st_size==part['bytes'] and sha(path)==part['sha256'],'corpus archive part differs')
            with path.open('rb') as f:shutil.copyfileobj(f,archive)
        archive.seek(0);return _restore(root,archive)

def _restore(root,source):
    root=Path(root).resolve()
    manifest=json.loads(local_path(root,'submission_manifest.json').read_text());asset=manifest['corpus_asset']
    dest=local_path(root,'data/processed/civic.sqlite')
    require(sha(source)==asset['sha256'],'corpus asset hash changed')
    expected={asset['member']:asset['database_sha256'],**asset['raw_sources']}
    with zipfile.ZipFile(source) as z:
        names=z.namelist();require(len(names)==len(set(names)),'duplicate ZIP members')
        require(set(names)==set(expected)|{'asset_manifest.json'},'unexpected ZIP members')
        for info in z.infolist():
            local_path(root,info.filename)
            require((info.external_attr>>16)&0o170000 != 0o120000,'symbolic ZIP member')
        require(json.loads(z.read('asset_manifest.json'))['files']==expected,'asset manifest differs')
    if dest.exists():
        require(sha(dest)==asset['database_sha256'],'existing DB differs from packaged snapshot; no overwrite')
    else:
        dest.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.restore-',dir=dest.parent);os.close(fd);tmp=Path(name)
        try:
            with zipfile.ZipFile(source) as z,z.open(asset['member']) as src,tmp.open('wb') as dst:shutil.copyfileobj(src,dst)
            require(sha(tmp)==asset['database_sha256'],'extracted corpus SHA mismatch')
            load_graph(root/'data/processed/graph.json',tmp)
            os.link(tmp,dest)
        finally:
            if tmp.exists():tmp.unlink()
    with zipfile.ZipFile(source) as z:
        for name,h in asset['raw_sources'].items():
            path=local_path(root,name)
            require(name.startswith(('data/graphability-audit/','data/raw/')),'unsafe raw source member')
            if path.exists():
                require(sha(path)==h,'existing raw source differs; no overwrite: '+name);continue
            path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmpname=tempfile.mkstemp(prefix='.restore-',dir=path.parent);os.close(fd);tmp=Path(tmpname)
            try:
                with z.open(name) as src,tmp.open('wb') as dst:shutil.copyfileobj(src,dst)
                require(sha(tmp)==h,'raw source hash mismatch: '+name);os.link(tmp,path)
            finally:
                if tmp.exists():tmp.unlink()
    return dest

def local_links(root):
    from html.parser import HTMLParser
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.urls=[]
        def handle_starttag(self,tag,attrs):
            self.urls.extend(v for k,v in attrs if k in ('href','src') and v)
    checked=0


    mf=root/'submission_manifest.json'
    paths=[local_path(root,n) for n in json.loads(mf.read_text())['files']] if mf.exists() else root.rglob('*')
    for doc in sorted(p for p in paths if p.suffix in ('.md','.html','.svg') and '.venv' not in p.parts):
        text=doc.read_text();parser=Links();parser.feed(text)
        urls=parser.urls+[m.group(1).strip('<>') for m in re.finditer(r'!?\[[^\]]*\]\(([^)]+)\)',text)]
        for target in urls:
            if target.startswith(('https://','http://','mailto:','data:','#')):continue
            target=target.split('#')[0].split('?')[0]
            p=local_path(root,target,doc.parent)
            require(p.exists(),f'broken link: {doc.relative_to(root)} -> {target}')
            checked+=1
    return checked

def preflight(root,manifest):
    for name,h in manifest['files'].items():
        p=local_path(root,name)
        require(p.is_file() and sha(p)==h,'file hash mismatch: '+name)
    return manifest

def verify_paths(g,ev,rows,max_hops,chunks):
    count=0
    for row in rows:
        candidates=set(row['candidate_chunk_ids']);require(candidates<=set(chunks),'unknown candidate')
        require(row['candidate_chunk_count']==len(candidates),'wrong candidate count')
        require([h['rank'] for h in row['hits']]==list(range(1,len(row['hits'])+1)),'noncontiguous ranking')
        require(len({h['chunk_id'] for h in row['hits']})==len(row['hits']),'duplicate hits')
        for hit in row['hits']:
            edges=hit['path_edges'];nodes=hit['path_nodes'];binding=hit['evidence_binding']
            require(nodes[0]==hit['seed_id'] and nodes[0] in {s['node_id'] for s in row['seeds']},'path seed mismatch')
            require(len(edges)+1==len(nodes) and len(edges)==hit['graph_hops']<=max_hops,'graph hop mismatch')
            hops=0
            for i,e in enumerate(edges):
                require(e['source']==nodes[i] and e['target']==nodes[i+1] and g.has_edge(e['source'],e['target'],e['key']),'invalid/discontinuous path')
                actual=g.edges[e['source'],e['target'],e['key']]
                require(actual['relation']==e['relation'],'relation label mismatch');hops+=actual['document_hop']
            require(hops==hit['document_hops'],'document hop mismatch')
            require(hit['path']==path_text(g,nodes,edges),'display path mismatch')
            owner=nodes[-1] if binding['kind']=='node' else edges[-1]['key']
            require(binding['owner_id']==owner,'evidence not on path endpoint')
            eid=binding['evidence_id'];require(eid in ev[binding['kind']+'_bindings'][owner],'missing evidence binding')
            record=ev['records'][eid];cid=hit['chunk_id']
            require(cid in record['chunk_ids'] and cid in candidates,'hit is not bound graph candidate')
            require(hit['evidence_quote']==record['quote'] and hit['evidence_span']==[record['start_char'],record['end_char']],'evidence quote/span mismatch')
            require(hit['document_id']==record['source_document_id']==chunks[cid]['document_id']==hit['evidence_source_document_id'],'source identity mismatch')
            require(hit['source_url']==record['source_url'],'source URL mismatch');count+=1
    return count


def verify_experiment(root,questions,chunks,graph,evidence):
    target=local_path(root,'results/current204');summary=json.loads((target/'summary.json').read_text())
    run=json.loads((target/'run_manifest.json').read_text());qs={q['question_id']:q for q in questions}
    require(summary['questions']==204 and summary['answerable_denominator']==177 and summary['diagnostic_only']==dict(clarify=17,abstain=10),'experiment denominator differs')
    require(run['public_projection']['questions_sha256']==sha(root/'questions.jsonl') and run['public_projection']['queries_sha256']==sha(root/'queries.jsonl'),'public input fingerprint differs')
    require(run['packaged_database_sha256']==sha(root/'data/processed/civic.sqlite'),'packaged experiment corpus differs')
    require(run['corpus_fingerprint']==graph.graph['corpus_fingerprint'] and run['corpus_hash']==graph.graph['corpus_hash'],'experiment logical corpus fingerprint differs')
    for name in ('graph.json','graph.evidence.json'):
        require(run['source_sha256']['data/processed/'+name]==sha(root/'data/processed'/name),'experiment Graph fingerprint differs')
    require(run['public_projection']['predictions_sha256']==sha(target/'predictions.jsonl'),'prediction projection fingerprint differs')
    require(summary['generation'] is None and summary['judge'] is None and run['answerability_classifier'] is None,'unexpected generated action evaluation')
    rows=read_jsonl(target/'predictions.jsonl');methods=list(summary['methods']);predictions={m:{} for m in methods};details=read_jsonl(target/'per_question.jsonl')
    require(set(methods)=={'bm25','dense','hybrid','graph_h0','graph_h1','graph_h2','graph_h3'},'method set differs')
    require(len(rows)==1428 and len(details)==204 and {r['question_id'] for r in details}==set(qs),'experiment row coverage differs')
    compact={r['question_id']:r for r in details};paths=0
    for r in rows:
        method=r['retriever'];qid=r['query_id'];require(method in predictions and qid in qs and qid not in predictions[method],'duplicate/unknown experiment row')
        hits=r['hits'];require(len(hits)<=10 and [h['rank'] for h in hits]==list(range(1,len(hits)+1)),'wrong result ranks')
        require(len({h['chunk_id'] for h in hits})==len(hits),'duplicate result chunk')
        require(all(h['chunk_id'] in chunks and math.isfinite(h['score']) for h in hits),'unknown result chunk or score')
        predictions[method][qid]=r
    query_types={typ:dict(question_count=sum(q['expected_action']=='answer' and q['question_type']==typ for q in questions),methods={}) for typ in sorted({q['question_type'] for q in questions if q['expected_action']=='answer'})}
    for method in methods:
        require(set(predictions[method])==set(qs),'missing method questions')
        counters=collections.Counter();sums={str(k):collections.Counter() for k in (1,3,5,10)};type_counts=collections.defaultdict(collections.Counter)
        if method.startswith('graph_'):paths+=verify_paths(graph,evidence,list(predictions[method].values()),int(method[-1]),chunks)
        for qid,q in qs.items():
            d=compact[qid];v=d['methods'][method];hits=predictions[method][qid]['hits']
            require(d['question']==q['question'] and d['gold_answer']==q.get('gold_answer') and d['expected_action']==q['expected_action'],'question detail differs')
            require(v['top1']==(v['top3'][0] if v['top3'] else None),'top1 differs from top3')
            require([(h['chunk_id'],h['rank'],h['score']) for h in v['top3']]==[(h['chunk_id'],h['rank'],h['score']) for h in hits[:3]],'compact top3 differs')
            require(d['generated_action'] is None,'unexpected generated action')
            if q['expected_action']!='answer':
                expected='diagnostic_'+q['expected_action'];require(v['status']==expected and not v['metrics'],'action diagnostic marked as evaluated');counters[expected]+=1;continue
            require(q['expected_answerability']=='answerable','unexpected retrieval denominator')
            groups=[set(e['acceptable_chunk_ids']) for e in q['evidence_requirements']];require(bool(groups),'empty answer Gold')
            for k in (1,3,5,10):
                ids=[h['chunk_id'] for h in hits[:k]];found=sum(bool(set(ids)&group) for group in groups)
                first=next((i for i,cid in enumerate(ids,1) if any(cid in group for group in groups)),None)
                metrics=dict(recall=found/len(groups),hit=int(found>0),all_evidence=int(found==len(groups)),mrr=1/first if first else 0)
                require(all(abs(v['metrics'][str(k)][key]-value)<1e-12 for key,value in metrics.items()),'per-question metric differs')
                sums[str(k)].update(metrics)
                if k==10:
                    status='success' if found==len(groups) else 'partial' if found else 'failure';require(v['status']==status,'retrieval status differs');counters[status]+=1;type_counts[q['question_type']][status]+=1
            missing={e['evidence_id'] for e in q['evidence_requirements'] if not set(h['chunk_id'] for h in hits)&set(e['acceptable_chunk_ids'])}
            require({e['evidence_id'] for e in v['missing_evidence']}==missing,'missing Gold diagnostics differ')
        expected=summary['methods'][method];require(dict(counters)==expected['status_counts'],'summary status counts differ')
        for k,values in sums.items():
            require(all(abs(expected['metrics'][k][key]-value/177)<1e-12 for key,value in values.items()),'summary metric differs')
        for typ,group in query_types.items():group['methods'][method]={status:type_counts[typ][status] for status in ('success','partial','failure')}
    require(summary['query_type_analysis']==query_types,'query type analysis differs')
    with (target/'per_question.csv').open(newline='') as f:
        csv_rows=list(csv.DictReader(f));require(len(csv_rows)==204 and {r['question_id'] for r in csv_rows}==set(qs),'CSV question coverage differs')
    transitions={str(h):sum([x['chunk_id'] for x in predictions[f'graph_h{h-1}'][qid]['hits']]!=[x['chunk_id'] for x in predictions[f'graph_h{h}'][qid]['hits']] for qid in qs) for h in range(1,4)}
    for h,count in transitions.items():require(summary['graph_hop_diagnostics']['transitions'][h]['top10_order_changed']==count,'Graph transition differs')
    return dict(methods=7,predictions=len(rows),retrieval_denominator=177,diagnostic_only=27,query_types=len(query_types),graph_paths=paths,metrics_recalculated=True,network_calls=0,top10_order_changes=transitions)

def verify_hit_details(block,hit):
    import html
    block=html.unescape(block)
    code=re.search(r'^```text\n(.*?)\n```',block,re.M|re.S)
    require(code is not None,'Graph hit identifiers missing')
    code=code.group(1)
    scalar={'Chunk':hit['chunk_id'],'Score':f"{hit['score']:.9f}",'Seed':hit['seed_id'],'Graph hops':str(hit['graph_hops']),'Document hops':str(hit['document_hops']),'Type path':hit['path'],'Evidence path':hit['evidence_path']}
    for label,value in scalar.items():
        require(re.findall(r'^'+re.escape(label)+r': (.*)$',code,re.M)==[value],'Graph hit field differs: '+label)
    nodes=re.search(r'^Path nodes:\n(.*?)\nPath edges:',code,re.M|re.S)
    require(nodes is not None and [n.strip() for n in nodes.group(1).splitlines()]==hit['path_nodes'],'Graph path nodes differ')
    edges=re.search(r'^Path edges:\n(.*?)\nEvidence:',code,re.M|re.S)
    require(edges is not None,'Graph path edges missing')
    edge_lines=[line.strip() for line in edges.group(1).splitlines()]
    expected=[]
    for e in hit['path_edges']:expected.extend([e['relation']+' · '+e['key'],e['source']+' → '+e['target']])
    require(edge_lines==(expected or ['없음 (node-only)']),'Graph path edges differ')
    binding=hit['evidence_binding'];span=hit['evidence_span']
    expected_binding={'kind':binding['kind'],'owner':binding['owner_id'],'id':binding['evidence_id'],'role':hit['evidence_role'],'document':hit['evidence_source_document_id'],'span':f'[{span[0]}, {span[1]})'}
    for label,value in expected_binding.items():
        require(re.findall(r'^  '+re.escape(label)+r': (.*)$',code,re.M)==[value],'Graph evidence field differs: '+label)
    quote=re.sub(r'\s+',' ',hit['evidence_quote']).strip();quote=quote if len(quote)<=100 else quote[:100]+'…'
    require(re.findall(r'^> (.*)$',block,re.M)==[quote],'Graph evidence quote differs')

def verify_full_graph_tables(root,stored):
    import html
    text=(root/'docs/03_graph_retrieval.md').read_text().split('<a id="all-graph-results"></a>',1)
    require(len(text)==2,'complete Graph section missing');text=text[1]
    sections=text.split('<a id="graph-record-details"></a>',1)
    require(len(sections)==2,'Graph detail section missing');text,detail_text=sections
    questions={q['question_id']:q for q in read_jsonl(root/'results/current204/per_question.jsonl')}
    graph=json.loads((root/'data/processed/graph.json').read_text());nodes={n['id']:n for n in graph['nodes']}
    documents={d['document_id']:d for d in json.loads((root/'data/source_index.json').read_text())['documents']}
    def norm(value):return re.sub(r'\s+',' ',str(value)).strip()
    def name(nid):
        n=nodes[nid];label=n['label']
        if n['type']=='Article':label=documents[n['source_document_id']]['title']+' · '+label
        return norm(label)+' ('+n['type']+')'
    question_ids=re.findall(r'<a id="result-(sj-[a-z]-\d+)"></a>',text)
    require(len(question_ids)==204 and {q.upper() for q in question_ids}==set(questions),'full Graph question coverage differs')
    for qid,q in questions.items():
        block=text.split('<a id="result-'+qid.lower()+'"></a>',1)[1].split('</details>',1)[0]
        require('<summary>'+qid+' · '+norm(q['question'])+'</summary>' in html.unescape(block),'full Graph question text differs')
        require('**유형**: '+q['question_type'] in block,'full Graph question type differs')
        require('**평가 행동**: '+q['expected_action'] in block,'full Graph action differs')
    pattern=r'<a id="result-(sj-[a-z]-\d+)-h([0-3])"></a>'
    marks=list(re.finditer(pattern,text,re.M));require(len(marks)==816,'full Graph record count differs')
    detail_marks=list(re.finditer(r'<a id="record-(sj-[a-z]-\d+)-h([0-3])"></a>',detail_text))
    require(len(detail_marks)==816,'Graph detail record count differs')
    detail_blocks={('graph_h'+mark.group(2),mark.group(1).upper()):detail_text[mark.end():detail_marks[i+1].start() if i+1<len(detail_marks) else len(detail_text)] for i,mark in enumerate(detail_marks)}
    require(len(detail_blocks)==816,'duplicate Graph detail record')
    keys=set();hit_count=0;empty_count=0;node_only=0
    for index,mark in enumerate(marks):
        qid,hop=mark.groups();qid=qid.upper()
        key=('graph_h'+hop,qid);require(key not in keys and key in stored,'duplicate/unknown Graph record');keys.add(key)
        record=stored[key];summary_block=text[mark.end():marks[index+1].start() if index+1<len(marks) else len(text)]
        require('**hop '+hop+'** · 반환 '+str(len(record['hits']))+'건 · 후보 '+str(record['candidate_chunk_count'])+'개 · 방문 노드 '+str(record['visited_node_count'])+'개' in summary_block,'Graph summary metadata differs')
        require(key in detail_blocks,'Graph detail record missing');block=detail_blocks[key]
        metadata=f"반환 {len(record['hits'])}건 · 후보 {record['candidate_chunk_count']}개 · 방문 노드 {record['visited_node_count']}개 · status={record['status']} · 탐색 제한 중단={'있음' if record['traversal_truncated'] else '없음'}"
        require(metadata in block,'full Graph metadata differs: '+str(key))
        seeds=['- '+norm(s['label'])+' · '+f"{s['score']:.6f}"+' · '+chr(96)+s['node_id']+chr(96) for s in record['seeds']] or ['- 없음']
        seed_block=re.search(r'\*\*출발점과 점수\*\*\n\n(.*?)(?=\n\n)',block,re.S)
        require(seed_block is not None and html.unescape(seed_block.group(1)).splitlines()==seeds,'full Graph seed differs')
        lines=[line for line in summary_block.splitlines() if re.match(r'^\| \d+ \| ',line)]
        require(len(lines)==len(record['hits']),'full Graph hit count differs: '+str(key))
        hit_marks=list(re.finditer(r'<a id="hit-(sj-[a-z]-\d+)-h([0-3])-r(\d+)"></a>',block))
        require(len(hit_marks)==len(record['hits']),'full Graph detail count differs: '+str(key))
        if not record['hits']:
            require(all('**빈 결과**' in b and '반환 Rank·Chunk·Path·Evidence는 없다.' in b for b in (block,summary_block)),'empty Graph result missing');empty_count+=1
        for i,(line,hit,hit_mark) in enumerate(zip(lines,record['hits'],hit_marks)):
            values=[html.unescape(v.strip()) for v in line.strip('|').split('|')]
            labels=[]
            for nid in hit['path_nodes']:
                n=nodes[nid];label=norm(n['locator'] if n['type']=='Article' else n['label']);labels.append(label if len(label)<=24 else label[:24]+'…')
            path_summary=' → '.join(labels)+(' · node-only' if not hit['path_edges'] else ' · '+str(hit['graph_hops'])+' hop')
            require(values==[str(hit['rank']),norm(hit['title']),path_summary],'full Graph summary row differs')
            require(hit_mark.groups()==(qid.lower(),hop,str(hit['rank'])),'full Graph hit anchor differs')
            detail=block[hit_mark.end():hit_marks[i+1].start() if i+1<len(hit_marks) else len(block)]
            require('**'+str(hit['rank'])+'위 · '+norm(hit['title'])+'**' in html.unescape(detail),'full Graph hit title differs')
            path=['- 출발: '+name(hit['path_nodes'][0])]
            path.extend('- '+e['relation']+' → '+name(e['target']) for e in hit['path_edges'])
            if not hit['path_edges']:path.append('- 관계 이동 없음 · node-only');node_only+=1
            actual_path=re.search(r'^(- 출발:.*?)\n\n>',html.unescape(detail),re.M|re.S)
            require(actual_path is not None and actual_path.group(1).splitlines()==path,'full Graph named path differs')
            verify_hit_details(detail,hit);hit_count+=1
    require(keys=={key for key in stored if key[0].startswith('graph_')},'full Graph method/question coverage differs')
    require(hit_count==6805 and empty_count==48,'full Graph stored counts differ')
    require(text.count('<details>')==text.count('</details>')==204,'full Graph question blocks differ')
    records_text=detail_text[detail_marks[0].start():]
    require(records_text.count('<details>')==records_text.count('</details>')==816,'full Graph detail blocks differ')
    return dict(full_graph_records=len(keys),full_graph_hits=hit_count,full_graph_empty=empty_count,full_graph_node_only=node_only)

def verify_documents(root,manifest):
    import html
    import xml.etree.ElementTree as ET
    expected={'01_graph_schema.md','02_graph_construct.md','03_graph_retrieval.md','04_fail_case_analysis.md'}
    require({str(p.relative_to(root/'docs')) for p in (root/'docs').rglob('*.md')}==expected,'assignment document set differs')
    retired={'docs/graph_schema.md','results/current204/REPORT.md','results/current204/QUESTION_ANALYSIS.md','results/current204/SUCCESS_DETAILS.md','results/current204/graph/GRAPH_DIAGNOSIS.md'}
    require(not retired.intersection(manifest['files']) and not any((root/n).exists() for n in retired),'retired document returned')
    require(manifest['experiment']['report']=='docs/04_fail_case_analysis.md','experiment report link differs')
    require(json.loads((root/'dataset.json').read_text())['current_evaluation']['report']=='docs/04_fail_case_analysis.md','dataset report link differs')
    text=(root/'docs/04_fail_case_analysis.md').read_text()
    rows=read_jsonl(root/'results/current204/per_question.jsonl')
    ids=[qid.upper() for qid in re.findall(r'<a id="case-(sj-[a-z]-\d+)"></a>',text)]
    require(len(ids)==len(set(ids))==204 and set(ids)=={r['question_id'] for r in rows},'consolidated case coverage differs')
    names={'bm25':'BM25','dense':'Dense','hybrid':'Hybrid',**{f'graph_h{h}':f'Graph {h}' for h in range(4)}}
    statuses={'success':'전체 발견','partial':'일부 발견','failure':'미발견'}
    for row in rows:
        block=text.split('<a id="case-'+row['question_id'].lower()+'"></a>',1)[1].split('</details>',1)[0]
        if row['expected_action']=='answer':
            for method,label in names.items():require(label+': '+statuses[row['methods'][method]['status']] in block,'case method status differs')
        else:require(('추가 확인' if row['expected_action']=='clarify' else '응답 보류')+' · 미평가' in block,'case diagnostic action differs')
    summary=json.loads((root/'results/current204/summary.json').read_text())
    methods=['bm25','dense','hybrid','graph_h0','graph_h1','graph_h2','graph_h3']
    labels=['BM25','Dense','Hybrid','Graph 0','Graph 1','Graph 2','Graph 3']
    for method,label in zip(methods,labels):
        value=summary['methods'][method];counts=value['status_counts'];metric=value['metrics']['10']
        line=f"| {label} | {counts['success']} ({counts['success']/177:.1%}) | {counts['partial']} | {counts['failure']} | {metric['recall']:.1%} | {metric['hit']:.1%} | {metric['mrr']:.3f} |"
        require(line in text,'document method metrics differ: '+method)
    for typ,group in summary['query_type_analysis'].items():
        token='('+chr(96)+typ+chr(96)+')'
        matching=[line for line in text.splitlines() if line.startswith('| ') and token in line]
        require(len(matching)==1,'document query type row differs: '+typ)
        values=[v.strip() for v in matching[0].strip('|').split('|')]
        require(values[1]==str(group['question_count']),'document query type denominator differs')
        require(values[2:]==['/'.join(str(group['methods'][m][s]) for s in ('success','partial','failure')) for m in methods],'document query type counts differ')
    diag=json.loads((root/'results/current204/graph/diagnostic_summary.json').read_text())['current204']
    for hop in range(4):
        m=summary['methods'][f'graph_h{hop}'];metric=m['metrics']['10']
        change='—' if hop==0 else str(diag['transitions'][str(hop)]['top10_order_changed'])+'/204'
        line=f"| {hop} | {diag['hops'][str(hop)]['mean_candidates']:.2f} | {change} | {m['status_counts']['success']}/177 | {metric['recall']:.2%} | {metric['hit']:.2%} | {metric['mrr']:.4f} |"
        require(line in text,'document hop metrics differ: '+str(hop))
    stored={(r['retriever'],r['query_id']):r for r in read_jsonl(root/'results/current204/predictions.jsonl')}
    samples=0;zero_hop=0
    retrieval=(root/'docs/03_graph_retrieval.md').read_text()
    for mark in re.finditer(r'<a id="sample-(sj-[a-z]-\d+)-(graph_h[0-3])-r(\d+)"></a>',retrieval):
        qid,method,rank=mark.groups();qid=qid.upper();rank=int(rank)
        block=retrieval[mark.end():].split('</details>',1)[0]
        hit=next(h for h in stored[method,qid]['hits'] if h['rank']==rank)
        require('<summary>'+qid+' · '+method+' · '+str(rank)+'위 · 경로와 근거</summary>' in block,'document sample rank differs')
        require('**'+re.sub(r'\s+',' ',hit['title']).strip()+'**' in html.unescape(block),'document sample title differs')
        require('경로: '+hit['evidence_path'] in html.unescape(block),'document sample path differs')
        verify_hit_details(block,hit)
        zero_hop+=hit['graph_hops']==0;samples+=1
    require(samples==5 and zero_hop==2,'document retrieval sample coverage differs')
    figures=list((root/'figures').glob('*.svg'));require(len(figures)==3,'three required subgraph figures missing')
    for p in figures:ET.parse(p)
    complete=verify_full_graph_tables(root,stored)
    return dict(core_documents=4,case_rows=204,method_metric_rows=7,query_type_rows=8,hop_metric_rows=4,retrieval_examples=samples,zero_hop_examples=zero_hop,figures=3,**complete)

def verify(root=ROOT):
    root=Path(root).resolve();m=preflight(root,json.loads(local_path(root,'submission_manifest.json').read_text()))
    documentation=verify_documents(root,m)
    links=local_links(root);db=restore(root);qs=read_jsonl(root/'questions.jsonl');ids={q['question_id'] for q in qs}
    require(len(qs)==len(ids)==204,'question count or unique IDs differs')
    require(read_jsonl(root/'queries.jsonl')==[dict(query_id=q['question_id'],question=q['question']) for q in qs],'query projection differs')
    with connect(root/'data/questions.sqlite',True) as c:
        require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','question database integrity')
        require(not c.execute('PRAGMA foreign_key_check').fetchall(),'question foreign keys')
        require({r['question_id']:json.loads(r['payload']) for r in c.execute('SELECT * FROM questions')}=={q['question_id']:q for q in qs},'question database payload differs')
        require(c.execute('SELECT count(*) FROM services').fetchone()[0]==40,'service count differs')
        require({r['service_id']:json.loads(r['payload']) for r in c.execute('SELECT * FROM services')}=={s['service_id']:s for s in read_jsonl(root/'data/services.jsonl')},'service database payload differs')
        gold_rows={ (r['question_id'],r['ordinal']):json.loads(r['payload']) for r in c.execute('SELECT * FROM gold') }
        require(gold_rows=={(q['question_id'],i):e for q in qs for i,e in enumerate(q.get('evidence_requirements',[]))},'normalized Gold differs')
        require({r['question_id'] for r in c.execute('SELECT question_id FROM memberships')}==ids,'intent membership differs')
    spans=0
    with connect(db,True) as c:
        require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','corpus integrity')
        require(not c.execute('PRAGMA foreign_key_check').fetchall(),'corpus foreign keys')
        docs={r['document_id']:dict(r) for r in c.execute('SELECT * FROM documents')}
        chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
        for q in qs:
            for e in q.get('evidence_requirements',[]):
                d=docs[e['document_id']];a=e['start_char'];b=e['end_char']
                require(d['body'][a:b]==e['quote'],'Gold quote differs: '+q['question_id'])
                require(bool(e['acceptable_chunk_ids']),'empty Gold chunks')
                for cid in e['acceptable_chunk_ids']:
                    r=chunks[cid];require(r['document_id']==d['document_id'] and r['start_char']<b and r['end_char']>a,'Gold chunk differs')
                spans+=1
        for r in c.execute('SELECT d.raw_path,d.raw_hash,r.payload FROM documents d JOIN raw_sources r USING(raw_hash)'):
            require(digest(r['payload'])==r['raw_hash']==sha(local_path(root,r['raw_path'])),'raw source differs')
    g,ev=load_graph(root/'data/processed/graph.json',db);graph=validate_graph(g,ev)
    require(sha(root/'data/services.jsonl')==g.graph['registry_sha256'],'Graph registry fingerprint differs')
    for name in m['files']:
        if not name.endswith('.py'):continue
        s=local_path(root,name).read_text();t=ast.parse(s)
        require(not any(x.type==tokenize.COMMENT for x in tokenize.generate_tokens(io.StringIO(s).readline)),'Python comment present')
        require(not any(ast.get_docstring(n) for n in ast.walk(t) if isinstance(n,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))),'Python docstring present')
    experiment=verify_experiment(root,qs,chunks,g,ev)
    return dict(documentation=documentation,experiment=experiment,questions=len(qs),intent_groups=m['counts']['intent_groups'],services=40,documents=len(docs),chunks=len(chunks),gold_spans=spans,links=links,graph=graph,files=len(m['files'])+1)
