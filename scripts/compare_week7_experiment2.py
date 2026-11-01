"""Reuse one full-pool BGE score cache for untuned factorial/diagnostic comparisons."""
from __future__ import annotations
import _bootstrap  # Make local src imports available.
import argparse
import json
import math
from pathlib import Path
import statistics
import time

from civic_link.common import ROOT, connect, digest, read_jsonl, write_json, write_jsonl, input_bytes, input_exists, input_path, read_json
from civic_link.evaluation.runner import retrieval_metrics
from civic_link.evaluation.behavior import evaluate_behavior
from civic_link.reranking.policies import stored_order
from civic_link.reranking.coverage import rank
from run_week7_experiment2 import DB, QUERIES, QUESTIONS, CORE, summarize


def graph_diagnostics(out):
    """The existing independent lexical-seed Graph retriever; no BGE calls."""
    from civic_link.graph.build import load_graph
    from civic_link.graph.retriever import GraphRetriever
    graph_path=ROOT/'data/graph/graph.json'
    identity={p.name:digest(input_bytes(p)) for p in (QUERIES,graph_path,
        graph_path.with_suffix('.evidence.json'),ROOT/'src/civic_link/graph/retriever.py')}
    meta_path=out/'graph_diagnostics.run.json'
    if meta_path.exists():
        meta=read_json(meta_path)
        # A historical cache records the original runner's source fingerprint.
        # Validate its frozen data inputs and outputs; package migration need not
        # change (or pretend to reproduce) the original execution's source hash.
        data_keys = ('queries.jsonl', 'graph.json', 'graph.evidence.json')
        if meta['status']!='completed' or any(meta['identity'][k]!=identity[k] for k in data_keys):
            raise ValueError('Graph diagnostic cache differs or is incomplete')
        for name,checksum in meta['predictions'].items():
            if digest(input_bytes((out/name)))!=checksum:raise ValueError('Graph cache changed')
        return meta
    start=time.perf_counter();graph,evidence=load_graph(graph_path,DB)
    engine=GraphRetriever(graph,DB,evidence)
    meta=dict(identity=identity,setup_seconds=time.perf_counter()-start,status='running',
        config=dict(top_k=100,seed_k=3,seed_threshold=.18,max_nodes=12000),
        conditions={},predictions={},model_calls=0)
    queries=read_jsonl(QUERIES)
    for hops in (0,1,2,3):
        rows,times=engine.run(queries,top_k=100,max_hops=hops)
        name=f'graph_lexical_h{hops}.jsonl';write_jsonl(out/name,rows)
        meta['predictions'][name]=digest(input_bytes((out/name)))
        meta['conditions'][f'graph_lexical_h{hops}']=dict(query_count=len(rows),
            query_seconds=sum(times)/1000,no_seeds=sum(not r['seeds'] for r in rows),
            traversal_truncated=sum(r['traversal_truncated'] for r in rows),
            retained_candidates=sum(len(r['hits']) for r in rows))
    meta['status']='completed';write_json(meta_path,meta)
    return meta


def cached_bge_order(prior, scores, limit=None):
    """Only the predeclared pool/gate and exact tie order depend on the formula."""
    if len(prior) != len(set(prior)) or any(c not in scores for c in prior):
        raise ValueError('invalid pool or missing cached score')
    if any(not math.isfinite(scores[c]) for c in prior):
        raise ValueError('nonfinite score')
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('invalid candidate budget')
    selected = prior if limit is None else prior[:limit]
    position = {cid:i for i,cid in enumerate(selected)}
    return sorted(selected, key=lambda cid:(-scores[cid], position[cid]))


def compare(out, destination):
    # This validates saved input text, fingerprints, all 608 outputs and full pools.
    summarize(out, destination)
    summary=read_json((destination/'summary.json'))
    summary['graph_diagnostics']=graph_diagnostics(out)
    per=read_json((destination/'per_question.json'))
    h3=read_jsonl(out/'h3.jsonl');coverage=read_jsonl(out/'coverage.jsonl');bge=read_jsonl(out/'bge.jsonl')
    with connect(DB,True) as db:chunks={r['chunk_id']:dict(r) for r in db.execute('select * from chunks')}
    definitions={
        'h3':'Hybrid+Graph; old .5R+.3C+.2Q; full pool',
        'h3_bge':'Hybrid+Graph; full pool BGE; old H3 breaks exact ties only',
        'coverage':'Hybrid+Graph; .3H+.5C+.2G plus overlap; full pool',
        'bge':'Hybrid+Graph; full pool BGE; coverage breaks exact ties only',
        'hybrid':'Original frozen Hybrid100 ranking; no Graph candidates',
        'coverage_no_graph_signal':'Same full pool; .3H+.5C+0G; unchanged overlap, no weight renormalization',
        'hybrid_bge':'Original Hybrid100 pool reranked by cached BGE; Hybrid order breaks ties',
        'h3_top50_bge':'Old H3 selects 50; cached BGE reranks only these 50; no tail',
        'coverage_top50_bge':'Coverage selects 50; cached BGE reranks only these 50; no tail',
        'graph_hybrid_seed':'Existing Hybrid-seeded Graph branch only; admitted Graph candidates ordered by native Graph rank; no fusion',
    }
    definitions.update({f'graph_lexical_h{h}':f'Existing independent lexical Service/Law seed Graph retriever; {h} hops; top100; no Hybrid input' for h in (0,1,2,3)})
    rankings={m:{} for m in definitions};tie_changes=[];gate_changes=[]
    for old,new,neural in zip(h3,coverage,bge,strict=True):
        qid=old['query_id']
        assert qid==new['query_id']==neural['query_id']
        scores={r['chunk_id']:r['score'] for r in neural['bge_scores']}
        old_order=stored_order(old);new_order=stored_order(new)
        assert len(scores)==len(neural['bge_scores']) and set(scores)==set(old_order)==set(new_order)
        hybrid=[c['chunk_id'] for c in sorted((c for c in old['candidates'] if c['hybrid_rank'] is not None),key=lambda c:c['hybrid_rank'])]
        assert len(hybrid)==100 and set(hybrid)<=set(scores)
        orders=dict(h3=old_order,coverage=new_order,hybrid=hybrid,
            h3_bge=cached_bge_order(old_order,scores),bge=cached_bge_order(new_order,scores),
            coverage_no_graph_signal=rank(old['candidates'],chunks,use_graph=False)[0],
            hybrid_bge=cached_bge_order(hybrid,scores),
            h3_top50_bge=cached_bge_order(old_order,scores,50),
            coverage_top50_bge=cached_bge_order(new_order,scores,50))
        assert orders['bge']==neural['ranked_chunk_ids']
        if orders['h3_bge'][:10]!=orders['bge'][:10]:tie_changes.append(qid)
        if set(old_order[:50])!=set(new_order[:50]):gate_changes.append(qid)
        for method,order in orders.items():rankings[method][qid]=order
        rankings['graph_hybrid_seed'][qid]=[c['chunk_id'] for c in sorted(
            (c for c in old['candidates'] if c['graph_rank'] is not None and not c['graph_excluded_by_cap']),key=lambda c:c['graph_rank'])]
    for hops in (0,1,2,3):
        name=f'graph_lexical_h{hops}'
        rankings[name]={r['query_id']:[h['chunk_id'] for h in r['hits']] for r in read_jsonl(out/(name+'.jsonl'))}
    comparisons=[('h3','coverage'),('coverage','bge'),('h3','h3_bge'),('h3_bge','bge'),
        ('hybrid','h3'),('hybrid','coverage'),('coverage_no_graph_signal','coverage'),
        ('hybrid_bge','bge'),('h3_top50_bge','coverage_top50_bge'),
        ('h3_top50_bge','h3_bge'),('coverage_top50_bge','bge'),
        ('hybrid','graph_lexical_h2'),('graph_lexical_h0','graph_lexical_h1'),
        ('graph_lexical_h1','graph_lexical_h2'),('graph_lexical_h2','graph_lexical_h3')]
    questions_all=read_jsonl(QUESTIONS)
    frozen_split=read_json((ROOT/'experiments/score_comparison/results/protocol.json'))['split']
    subsets={'all':questions_all,'balanced':read_jsonl(CORE),
             'original204':[q for q in questions_all if q['row_origin']=='historical204']}
    subsets.update({name:[q for q in questions_all if frozen_split[q['question_id']]['split']==name]
                    for name in ('development','validation')})
    summary['candidate_ceiling']={}
    for subset,questions in subsets.items():
        ids={q['question_id'] for q in questions}
        summary['subsets'].setdefault(subset,{})
        per.setdefault(subset,{})
        summary['candidate_ceiling'][subset]={}
        for method,lookup in rankings.items():
            predictions=[dict(query_id=qid,retriever=method,hits=[dict(query_id=qid,retriever=method,
                chunk_id=cid,document_id=chunks[cid]['document_id'],rank=i,score=1/i)
                for i,cid in enumerate(order,1)]) for qid,order in lookup.items() if qid in ids]
            result,values,_=retrieval_metrics(questions,predictions,set(chunks))
            if method in summary['subsets'][subset]:
                assert result['retrieval']['overall']==summary['subsets'][subset][method]['overall']
            summary['subsets'][subset][method]=result['retrieval']
            per[subset][method]={r['query_id']:r for r in values}
            summary['candidate_ceiling'][subset][method]=sum(
                all(set(e['acceptable_chunk_ids']).intersection(lookup[q['question_id']])
                    for e in q['evidence_requirements'])
                for q in questions if q['expected_action']=='answer')
        for before,after in comparisons:
            gain=[];loss=[]
            for qid,old in per[subset][before].items():
                diff=per[subset][after][qid]['metrics']['10']['all_evidence']-old['metrics']['10']['all_evidence']
                if diff>0:gain.append(qid)
                elif diff<0:loss.append(qid)
            summary['comparisons'][subset+':'+before+'->'+after]=dict(gained=gain,lost=loss)
    summary['conditions']=definitions
    summary['diagnostic_design']=dict(top_n=50,selection='fixed before full inference completed; not tuned on gold',
        full_bge_formula_role='only exact tie order; no score blending',
        full_bge_top10_order_different_ids=tie_changes,top50_pool_different_ids=gate_changes,
        additional_model_calls=0,top50_runtime_measured=False,
        graph_signal_control='Graph-generated candidates retained; isolates G score, not Graph candidate generation')
    score_max={r['query_id']:max(s['score'] for s in r['bge_scores']) for r in bge}
    behavior={}
    for subset,questions in subsets.items():
        audit,_=evaluate_behavior(questions)
        distributions={}
        for action in ('answer','clarify','abstain'):
            values=sorted(score_max[q['question_id']] for q in questions if q['expected_action']==action)
            quartiles=statistics.quantiles(values,n=4,method='inclusive')
            distributions[action]=dict(n=len(values),min=min(values),q25=quartiles[0],
                median=statistics.median(values),q75=quartiles[2],max=max(values))
        behavior[subset]=dict(action_evaluation=audit,bge_max_score_distribution=distributions,
            interpretation='score overlap diagnostic only; no action classifier, generated response, threshold selection or slot verification executed')
    write_json(destination/'behavior_audit.json',behavior)
    write_json(destination/'summary.json',summary)
    write_json(out/'diagnostic_metrics.json',per)
    compact={qid:{method:dict(complete_at_10=rows[qid]['metrics']['10']['all_evidence'],
        recall_at_10=rows[qid]['metrics']['10']['recall'],mrr_at_100=rows[qid]['metrics']['100']['mrr'])
        for method,rows in per['all'].items()} for qid in per['all']['h3']}
    write_json(destination/'per_question.json',compact)
    write_json(out/'diagnostic_rankings.json',rankings)
    for subset,methods in summary['subsets'].items():
        print(subset)
        for method,result in methods.items():
            overall=result['overall'];m=overall['metrics']['10'];n=overall['question_count']
            print(method,round(m['all_evidence']*n),n,m['recall'],m['mrr'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--summary-dir',type=Path,required=True)
    args=parser.parse_args();compare(args.output_dir,args.summary_dir)
