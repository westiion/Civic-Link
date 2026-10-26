"""Audit stored predictions and build an all-question appendix. No retrieval/API calls."""
import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
parser.add_argument('--run', default='20261027_052016')
parser.add_argument('--output-dir', type=Path, required=True)
args=parser.parse_args()
ROOT=args.root.resolve()
RUN=ROOT/'results/week4_run'/args.run
OUT=args.output_dir.resolve()
OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluate import evaluate

def js(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p):
    with p.open(encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def compact(s, n=160):
    text=' '.join(str(s).split()).replace('|','／').replace('<','&lt;').replace('>','&gt;')
    return text if len(text)<=n else text[:n]+'…'
def status(q,row):
    if q['expected_answerability']!='answerable': return '진단용'
    h={x['chunk_id'] for x in row['hits'][:10]}
    n=sum(bool(set(g['acceptable_chunk_ids']) & h) for g in q['evidence_requirements'])
    return '성공' if n==len(q['evidence_requirements']) else '부분' if n else '실패'
def rank(ids, ordered):
    return next((i for i,c in enumerate(ordered,1) if c in ids),None)
def rlabel(value): return str(value) if value is not None else '없음'

questions=list(rows(ROOT/'data/questions.jsonl')); qmap={q['question_id']:q for q in questions}
scored=[q for q in questions if q['expected_answerability']=='answerable']
with sqlite3.connect(f'file:{(ROOT/"data/processed/civic.sqlite").as_posix()}?mode=ro',uri=True) as c:
    c.row_factory=sqlite3.Row
    chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
graph=js(ROOT/'data/processed/graph.json'); nodes={n['id']:n for n in graph['nodes']}
labels={i:n['label'] for i,n in nodes.items()}
spec={m:RUN/f'{m}.predictions.jsonl' for m in ['bm25','dense','hybrid','graph_h0','graph_h1','graph_h2','graph_h3']}
spec.update({m:RUN/f'week4/predictions/{m}.jsonl' for m in ['hybrid_graph','h1_lexical_uniform','h1_hybrid_uniform','h2_before','h2_after','h3_rrf','h3_feature']})
metrics={};preds={}; details={q['question_id']:{} for q in questions};hashes={};candidate_totals={}
for name,path in spec.items():
    minimal=[];by_id={};tot=Counter();gains=Counter();lat=None
    for row in rows(path):
        q=qmap[row['query_id']];qid=q['question_id']
        ids=[h['chunk_id'] for h in row['hits'][:10]]
        d={'status':status(q,row),'top10':ids,'seeds':row.get('seeds',[]),'diagnostics':row.get('diagnostics',{}),'groups':[]}
        cand={x['chunk_id']:x for x in row.get('candidates',[])}
        pool={cid for cid,v in cand.items() if v['hybrid_rank'] is not None or (v['graph_rank'] is not None and not v['graph_excluded_by_cap'])}
        if name.startswith('graph_h'):pool=set(row['candidate_chunk_ids'])
        for g in q['evidence_requirements']:
            accepted=set(g['acceptable_chunk_ids']);matches=[v for cid,v in cand.items() if cid in accepted]
            def smallest(key):return min((v[key] for v in matches if v[key] is not None),default=None)
            entry={'rank10':rank(accepted,ids),'in_pool':bool(accepted&pool),
                'hybrid_rank':smallest('hybrid_rank'),'graph_rank':smallest('graph_rank'),
                'fusion_rank':smallest('fusion_rank'),'rrf_rank':smallest('rrf_rank'),
                'graph_kept':any(v['graph_rank'] is not None and not v['graph_excluded_by_cap'] for v in matches),
                'graph_seen':bool(matches and any(v['paths'] for v in matches)),
                'corpus_present':bool(accepted&chunks.keys())}
            paths=[p for v in matches for p in v['paths']]
            if paths:
                best=max(paths,key=lambda p:p['score'])
                entry['best_path']={k:best[k] for k in ['seed_id','path','path_nodes','path_edges','graph_hops','evidence_binding','evidence_quote','score_components']}
            if q['expected_answerability']=='answerable':
                stage='top10' if entry['rank10'] else 'pool_but_dropped' if entry['in_pool'] else 'outside_top10_pool_unmeasured' if name in ('bm25','dense','hybrid') else 'outside_pool'
                tot[stage]+=1
            d['groups'].append(entry)
        if row.get('diagnostics'):
            if row['diagnostics']['truncation_reasons']:tot['truncated_queries']+=1
            if not row['seeds']:tot['no_seed_queries']+=1
        if name.startswith('graph_h'):
            d['candidate_chunk_ids']=row['candidate_chunk_ids']
            d['traversal_truncated']=row['traversal_truncated']
            d['visited_node_count']=row['visited_node_count']
        minimal.append({'query_id':qid,'retriever':row['retriever'],'hits':row['hits'][:10]})
        by_id[qid]=minimal[-1];details[qid][name]=d
    report,cases=evaluate(questions,minimal,set(chunks))
    metrics[name]={'retrieval':report['retrieval'],'status_counts':dict(Counter(x['retrieval_status_at_10'] for x in cases))}
    preds[name]=by_id;candidate_totals[name]=dict(tot);hashes[str(path.relative_to(ROOT))]=sha(path)
    if name in ('bm25','dense','hybrid','graph_h0','graph_h1','graph_h2','graph_h3'):
        stored=js(RUN/f'{name}.metrics.json');assert report['retrieval']==stored['retrieval']
        assert js(RUN/f'{name}.run.json')['predictions_sha256']==sha(path)
    else:
        assert js(path.with_suffix('.run.json'))['predictions_sha256']==sha(path)
    print('Recomputed',name,metrics[name]['status_counts'],dict(tot),flush=True)

# Exported Hybrid candidate pool must preserve the scored Top-10.
hybrid100={r['query_id']:r for r in rows(RUN/'hybrid_candidates.jsonl')}
for qid,row in hybrid100.items():
    assert row['hits'][:10]==preds['hybrid'][qid]['hits']
    for g,d in zip(qmap[qid]['evidence_requirements'],details[qid]['hybrid']['groups']):
        d['rank100']=rank(set(g['acceptable_chunk_ids']),[h['chunk_id'] for h in row['hits']])
        d['in_pool']=d['rank100'] is not None
candidate_totals['hybrid']=dict(Counter('top10' if g['rank10'] else 'pool_but_dropped' if g['in_pool'] else 'outside_pool' for q in scored for g in details[q['question_id']]['hybrid']['groups']))

# Comparisons across query types and graph depths use exactly the same question IDs.
pairs=[('hybrid','hybrid_graph'),('hybrid','h1_hybrid_uniform'),('hybrid','h3_feature'),('h1_lexical_uniform','h1_hybrid_uniform'),('h2_before','h2_after'),('h3_rrf','h3_feature')]
transitions={}
for before,after in pairs:
    change=Counter();complete=Counter();ids={k:[] for k in ['improved','worsened','same_count_changed_groups']}
    for q in scored:
        qid=q['question_id'];a=details[qid][before];b=details[qid][after]
        aa={i for i,x in enumerate(a['groups'],1) if x['rank10']};bb={i for i,x in enumerate(b['groups'],1) if x['rank10']}
        label='improved' if len(bb)>len(aa) else 'worsened' if len(bb)<len(aa) else 'same_count_changed_groups' if aa!=bb else 'unchanged'
        change[label]+=1
        if label in ids:ids[label].append(qid)
        complete[f"{a['status']}→{b['status']}"]+=1
    transitions[f'{before}__{after}']={'groups_change':dict(change),'status_change':dict(complete),'ids':ids}
hops=[]
for h in range(4):
    name=f'graph_h{h}';d=[details[q['question_id']][name] for q in questions]
    item={'hop':h,'mean_candidates':sum(len(x['candidate_chunk_ids']) for x in d)/len(d),'truncated_queries':sum(x['traversal_truncated'] for x in d)}
    if h:
        counter=Counter()
        for q in questions:
            a=details[q['question_id']][f'graph_h{h-1}'];b=details[q['question_id']][name]
            counter['top10_order_changed']+=a['top10']!=b['top10'];counter['top10_set_changed']+=set(a['top10'])!=set(b['top10'])
            counter['candidate_additions']+=len(set(b['candidate_chunk_ids'])-set(a['candidate_chunk_ids']))
            if q['expected_answerability']=='answerable':
                for ga,gb in zip(a['groups'],b['groups']):
                    counter['newly_in_pool_groups']+=not ga['in_pool'] and gb['in_pool']
                    counter['gained_top10_groups']+=not ga['rank10'] and bool(gb['rank10'])
                    counter['lost_top10_groups']+=bool(ga['rank10']) and not gb['rank10']
        item['changes']=dict(counter)
    hops.append(item)
funnel={}
for name in ['hybrid_graph','h1_hybrid_uniform','h3_feature']:
    cats=Counter();ids={}
    for q in scored:
        qid=q['question_id'];d=details[qid][name]
        if d['status']=='성공':continue
        missed=[x for x in d['groups'] if x['rank10'] is None]
        label='all_missing_in_pool' if all(x['in_pool'] for x in missed) else 'all_missing_outside_pool' if all(not x['in_pool'] for x in missed) else 'mixed'
        cats[label]+=1;ids.setdefault(label,[]).append(qid)
    funnel[name]={'question_categories':dict(cats),'ids':ids}
facts={'validation':{'scored':len(scored),'diagnostic':len(questions)-len(scored),'method_results_recomputed':len(spec),'no_api_calls':True,'hybrid_top10_matches_export':True},
       'metrics':metrics,'transitions':transitions,'hops':hops,'candidate_group_stages':candidate_totals,'failure_funnel':funnel,'input_sha256':hashes}
for name,comp,side in [('hybrid_graph','baseline','after'),('h1_lexical_uniform','h1','before'),('h1_hybrid_uniform','h1','after'),('h2_before','h2','before'),('h2_after','h2','after'),('h3_rrf','h3','before'),('h3_feature','h3','after')]:
    assert metrics[name]['retrieval']==js(RUN/f'week4/comparisons/{comp}/{side}.metrics.json')['retrieval']
for rel in ['data/questions.jsonl','data/queries.jsonl','data/intent_groups.jsonl','data/processed/graph.json','data/processed/graph.evidence.json','scripts/evaluate.py','scripts/hybrid_graph.py','scripts/run_week4.py','scripts/tokenization.py']:
    hashes[rel]=sha(ROOT/rel)
manifest=js(RUN/'run_manifest.json')
for key,path in [('queries','data/queries.jsonl'),('questions','data/questions.jsonl'),('graph','data/processed/graph.json'),('graph_evidence','data/processed/graph.evidence.json')]:
    assert manifest['source_sha256'][key]==sha(ROOT/path)
for path in (RUN/'week4/predictions').glob('*.run.json'):
    meta=js(path)
    for name,h in meta['source_sha256'].items():assert sha(ROOT/'scripts'/name)==h
    assert meta['hybrid_run']==js(RUN/'hybrid_candidates.run.json')
facts['validation'].update(all_14_metric_reports_match=True,current_question_graph_source_hashes_match=True,extension_source_hashes_match=True)
(OUT/'analysis_facts.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(OUT/'question_details.json').write_text(json.dumps(details,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

name_ko={'bm25':'BM25','dense':'Dense','hybrid':'Hybrid','graph_h0':'Graph 0-hop','graph_h1':'Graph 1-hop','graph_h2':'Graph 2-hop','graph_h3':'Graph 3-hop','hybrid_graph':'Hybrid+Graph','h1_hybrid_uniform':'H1 Hybrid 균등','h3_feature':'H3 재정렬'}
lines=['# Week4 전체 질문 검색 경로', '', '[분석 보고서](REPORT.md) · [계산 근거](analysis_facts.json)', '',
       '204개 질문을 빠짐없이 수록했다. 성공은 Top-10에서 정답 근거 그룹을 모두 회수했다는 뜻이며, 답변 생성의 정확도를 뜻하지 않는다. 27개 확인 필요·범위 밖 질문은 진단용으로 분리한다.', '',
       '근거 순위의 ‘없음’은 해당 후보 목록에 없다는 뜻이다. Graph 후보 순위는 확장 후보 내부 순위, 결합 순위는 Hybrid와 Graph를 합친 최종 순위다. Gold와 서비스 ID는 사후 분석에만 사용했다.', '']
for q in questions:
    qid=q['question_id'];d=details[qid];a=d['hybrid'];b=d['hybrid_graph'];c=d['h3_feature']
    lines.extend([f'<a id="{qid.lower()}"></a>', '<details>', f'<summary>{qid} · {compact(q["question"],95)} · Hybrid {a["status"]} → 확장 {b["status"]} → H3 {c["status"]}</summary>', ''])
    lines += [f'**질문:** {compact(q["question"],1000)}', '', f'유형 `{q["question_type"]}` · 평가용 문서 hop {q.get("document_hops", "미지정")} · 기대 동작 `{q["expected_action"]}`', '']
    if q['expected_answerability']!='answerable':
        lines += [f'**진단용:** {compact(q.get("unanswerable_reason"))}. 검색 결과를 반환했다는 이유로 성공으로 계산하지 않는다. 현재 파이프라인에는 확인 질문·응답 보류 판정과 답변 생성이 없다.', '']
    else:
        lines += [f'**정답 요지:** {compact(q["gold_answer"],380)}', '', '| 검색 방식 | 근거 회수 / 필요 | 판정 |', '|---|---:|---|']
        for name,label in name_ko.items():
            value=d[name];n=sum(x['rank10'] is not None for x in value['groups'])
            lines.append(f'| {label} | {n}/{len(q["evidence_requirements"])} | {value["status"]} |')
        lines += ['', 'H1 명칭→Hybrid 시작점: '+d['h1_lexical_uniform']['status']+'→'+d['h1_hybrid_uniform']['status']+' · H2 보완 전→후: '+d['h2_before']['status']+'→'+d['h2_after']['status'], '']
    seeds=b['seeds']
    entry='; '.join(f'{compact(labels.get(s["node_id"],s["node_id"]),42)}(Hybrid {s["hybrid_rank"]}위 청크)' for s in seeds) or '선택된 시작점 없음'
    diag=b['diagnostics']
    lines += ['**기본 확장 과정:** Hybrid 상위 10개 청크 → '+entry+' → 2-hop 탐색 → Graph 후보 '+str(diag.get('graph_candidates',0))+'개 중 '+str(diag.get('graph_kept',0))+'개 사용 → Hybrid 100개와 RRF 결합 → Top-10.', '']
    lexical_entry='; '.join(compact(labels.get(s['node_id'],s['node_id']),45) for s in d['graph_h1']['seeds']) or '없음'
    h1_entry='; '.join(compact(labels.get(s['node_id'],s['node_id']),45) for s in d['h1_lexical_uniform']['seeds']) or '없음'
    lines += ['기존 Graph 단독 시작점: '+lexical_entry+'. H1 비교의 명칭 기반 시작점: '+h1_entry+'. H1 Hybrid 조건은 위 기본 확장과 같은 시작점을 균등 가중치로 평가한다.', '']
    if q['evidence_requirements']:
        lines += ['| 근거 | 원문 위치 | Hybrid 100내 순위 | Graph내 순위 | 확장 결합 순위 | H3 순위 | 확장 단계 판정 |', '|---|---|---:|---:|---:|---:|---|']
        for i,g in enumerate(q['evidence_requirements']):
            ga=a['groups'][i];gb=b['groups'][i];gc=c['groups'][i]
            stage='Top-10 회수' if gb['rank10'] else '후보에는 있으나 Top-10 밖' if gb['in_pool'] else '최종 후보 풀 밖'
            lines.append(f'| G{i+1} | {compact(g["source_alias"]+" / "+g["locator"],65)} | {rlabel(ga.get("rank100"))} | {rlabel(gb["graph_rank"])} | {rlabel(gb["fusion_rank"])} | {rlabel(gc["fusion_rank"])} | {stage} |')
        lines.append('')
        for i,g in enumerate(q['evidence_requirements']):
            gb=b['groups'][i];bp=gb.get('best_path')
            lines.append(f'**G{i+1} 원문:** [{compact(g["source_alias"]+" / "+g["locator"],90)}]({g["source_url"]}) · 허용 청크 '+', '.join('`'+cid+'`' for cid in g['acceptable_chunk_ids']))
            lines.append('')
            if bp:
                path=labels[bp['path_nodes'][0]]
                for e in bp['path_edges']:path+=' → '+e['relation']+' → '+labels[e['target']]
                lines += ['그래프에서 이 근거로 가는 대표 경로: '+compact(path,400)+f' ({bp["graph_hops"]}-hop).', '', '경로에 붙은 원문 발췌(청크 회수와 별개로, 정답 명제를 직접 입증하는지는 검수 필요):', '', '> '+compact(bp['evidence_quote'],200), '']
            else:lines += ['기본 확장 로그에 이 정답 청크로 연결되는 경로가 없다. Hybrid 순위와 후보 풀 포함 여부를 함께 확인한다.', '']
    if b['status'] in ('부분','실패','진단용'):
        lines += ['**확장의 실제 상위 3개:** '+ ' / '.join(str(h['rank'])+'위 '+compact(h['title'],75) for h in preds['hybrid_graph'][qid]['hits'][:3]), '']
    lines += ['</details>', '']
(OUT/'QUESTION_TRACES.md').write_text('\n'.join(lines),encoding='utf-8')
assert sum(x=='<details>' for x in lines)==204
assert sum(x=='</details>' for x in lines)==204
print(json.dumps({'funnel':funnel,'hops':hops},ensure_ascii=False,indent=2))
