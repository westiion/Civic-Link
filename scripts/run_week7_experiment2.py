"""Fixed-pool H3 / coverage / coverage then BGE comparison. No gold enters scoring."""
from __future__ import annotations
import _bootstrap  # Make local src imports available.
import argparse
from dataclasses import asdict
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import time

from civic_link.common import ROOT, connect, digest, read_jsonl, write_json, write_jsonl, input_bytes, input_exists, input_path, read_json
from civic_link.reranking.policies import stored_order

QUERIES = ROOT/'data/benchmark/queries.jsonl'
QUESTIONS = ROOT/'data/benchmark/questions.jsonl'
CORE = ROOT/'data/benchmark/diagnostic480/questions.jsonl'
DB = ROOT/'data/corpus/civic.sqlite'
MODEL = 'BAAI/bge-reranker-v2-m3'


def ordered_result(row, scores, chunks):
    """BGE alone orders the unchanged allowed pool; coverage resolves exact ties."""
    prior = stored_order(row)
    if len(scores) != len(prior) or any(not math.isfinite(x) for x in scores):
        raise ValueError('nonfinite or incomplete scores')
    if len(prior) != len(set(prior)) or not set(prior) <= set(chunks):
        raise ValueError('invalid candidate pool')
    score_map = dict(zip(prior, scores))
    priority = {cid:i for i,cid in enumerate(prior)}
    order = sorted(prior, key=lambda cid:(-score_map[cid], priority[cid]))
    return dict(query_id=row['query_id'], retriever='coverage_bge', ranked_chunk_ids=order,
        bge_scores=[dict(chunk_id=cid,score=score_map[cid]) for cid in prior],
        hits=[dict(query_id=row['query_id'],retriever='coverage_bge',chunk_id=cid,
                   document_id=chunks[cid]['document_id'],rank=i,score=score_map[cid])
              for i,cid in enumerate(order[:10],1)])


def prepare(out):
    from civic_link.graph.build import load_graph
    from civic_link.graph.retriever import GraphRetriever
    from civic_link.retrieval.hybrid_graph import Config, load_inputs, run_frozen
    from civic_link.reranking.coverage import apply
    if out.exists():raise ValueError('choose a new run directory')
    out.mkdir(parents=True)
    start=time.perf_counter()
    graph_path=ROOT/'data/graph/graph.json'
    graph,evidence=load_graph(graph_path,DB)
    cfg=Config(rerank='feature')
    queries,lookup,provenance=load_inputs(QUERIES,ROOT/'data/retrieval/hybrid_candidates.jsonl.gz',
        ROOT/'data/retrieval/hybrid_candidates.run.json',graph,cfg)
    engine=GraphRetriever(graph,DB,evidence)
    setup=time.perf_counter()-start
    start=time.perf_counter()
    rows,settings=run_frozen(engine,queries,lookup,provenance,cfg,out/'h3.jsonl',graph_path)
    baseline=time.perf_counter()-start
    revised=[];times=[]
    for row in rows:
        start=time.perf_counter();result=apply(row,engine.chunks);times.append(time.perf_counter()-start)
        assert set(stored_order(row))==set(stored_order(result))
        revised.append(result)
    write_jsonl(out/'coverage.jsonl',revised)
    write_json(out/'coverage.run.json',dict(status='completed',query_count=len(rows),
        queries_sha256=digest(input_bytes(QUERIES)),predictions_sha256=digest(input_bytes((out/'coverage.jsonl'))),
        scoring_policy='coverage-path-overlap',api_calls=0))
    sizes=[len(stored_order(r)) for r in rows]
    protocol=dict(query_count=len(rows),queries_sha256=digest(input_bytes(QUERIES)),
        questions_sha256=digest(input_bytes(QUESTIONS)),core_sha256=digest(input_bytes(CORE)),
        corpus_hash=provenance['corpus_hash'],candidate_pool='same full allowed Hybrid100 union Graph<=100; no BGE cutoff',
        candidate_pairs=sum(sizes),candidate_min=min(sizes),candidate_max=max(sizes),candidate_mean=sum(sizes)/len(sizes),
        config=asdict(cfg),bge_protocol=dict(model=MODEL,precision='float16',device='mps',batch_size=32,
            max_length=512,truncation='only_second',passage='title + newline + text',ranking='raw BGE logits descending; exact ties use coverage order',
            coverage_role='orders input and exact ties only; no top-N gate; F is not blended with BGE'),
        timing=dict(setup_seconds=setup,common_graph_and_h3_seconds=baseline,
            common_graph_and_h3_query_seconds=sum(settings['query_latency_ms'])/1000,
            coverage_incremental_seconds=sum(times),coverage_per_query_seconds=times),
        source_sha256={p.name:digest(input_bytes(p)) for p in (Path(__file__),ROOT/'src/civic_link/retrieval/hybrid_graph.py',ROOT/'src/civic_link/reranking/coverage.py')},
        independent_holdout=False,api_inference_calls=0)
    write_json(out/'protocol.json',protocol)
    print(json.dumps(protocol,ensure_ascii=False,indent=2))


def infer(out, model_dir, revision, name='bge', limit=None, dtype='float16', batch_size=32):
    import torch
    if dtype not in ('float16','float32') or batch_size<1:
        raise ValueError('unsupported precision or batch')
    if model_dir.name != revision:
        raise ValueError('use the pinned Hugging Face snapshot directory')
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    if not torch.backends.mps.is_available():raise RuntimeError('MPS unavailable; no silent device change')
    # Only the macOS inference path needs resource; cached-result analysis also
    # runs on Windows, where that standard-library module is unavailable.
    import resource
    torch.set_num_threads(4)
    torch.manual_seed(0)
    protocol=read_json((out/'protocol.json'))
    if protocol['questions_sha256']!=digest(input_bytes(QUESTIONS)):raise ValueError('gold changed after prepare')
    queries=read_jsonl(QUERIES);lookup={q['query_id']:q['question'] for q in queries}
    rows=read_jsonl(out/'coverage.jsonl')
    if limit is not None:rows=rows[:limit]
    with connect(DB,True) as db:chunks={r['chunk_id']:dict(r) for r in db.execute('select * from chunks')}
    path=out/(name+'.jsonl');meta_path=out/(name+'.run.json')
    identity=dict(model=MODEL,revision=revision,queries_sha256=digest(input_bytes(QUERIES)),
        coverage_sha256=digest(input_bytes((out/'coverage.jsonl'))),source_sha256=digest(input_bytes(Path(__file__))),
        device='mps',dtype=dtype,batch_size=batch_size,max_length=512,truncation='only_second',
        attention_implementation='sdpa',passage='title + newline + text',limit=limit)
    old_meta=read_json(meta_path) if meta_path.exists() else None
    existing=read_jsonl(path) if path.exists() else []
    if old_meta and old_meta['identity']!=identity:raise ValueError('resume inputs/configuration differ')
    if existing and not old_meta:raise ValueError('output without provenance')
    if len(existing)>len(rows):raise ValueError('excess checkpoint rows')
    for done,source in zip(existing,rows):
        if done['query_id']!=source['query_id'] or set(done['ranked_chunk_ids'])!=set(stored_order(source)):
            raise ValueError('checkpoint mismatch')
    if len(existing)==len(rows) and old_meta and old_meta['status']=='completed':
        print('already completed');return
    meta=dict(status='running',identity=identity,queries_sha256=identity['queries_sha256'],query_count=len(existing),
        packages={x:importlib.metadata.version(x) for x in ('torch','transformers','tokenizers','huggingface-hub','safetensors')},
        platform=platform.platform(),machine=platform.machine(),api_inference_calls=0,download_included_in_timing=False,
        model_load_segments=(old_meta or {}).get('model_load_segments',[]))
    write_json(meta_path,meta)
    start=time.perf_counter()
    tokenizer=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False)
    model=AutoModelForSequenceClassification.from_pretrained(model_dir,local_files_only=True,
        trust_remote_code=False,use_safetensors=True,dtype=getattr(torch,dtype),attn_implementation='sdpa')
    model=model.to('mps').eval();torch.mps.synchronize()
    meta['model_load_segments'].append(time.perf_counter()-start)
    meta['attention_implementation_actual']=model.config._attn_implementation
    meta['parameter_count']=sum(p.numel() for p in model.parameters())
    write_json(meta_path,meta)
    print(json.dumps(dict(event='model_loaded',seconds=meta['model_load_segments'][-1],parameters=meta['parameter_count'])),flush=True)
    start_all=time.perf_counter()
    with path.open('a',encoding='utf-8') as stream,torch.inference_mode():
        for index,row in enumerate(rows[len(existing):],len(existing)):
            start=time.perf_counter();order=stored_order(row);query=lookup[row['query_id']]
            passages=[chunks[c]['title']+'\n'+chunks[c]['text'] for c in order]
            qlen=len(tokenizer.encode(query,add_special_tokens=False))
            if qlen+tokenizer.num_special_tokens_to_add(pair=True)>=512:raise ValueError('query exceeds input budget')
            lengths=[qlen+len(tokenizer.encode(p,add_special_tokens=False,verbose=False))+tokenizer.num_special_tokens_to_add(pair=True) for p in passages]
            encoded=tokenizer([query]*len(order),passages,truncation='only_second',max_length=512,padding=False)
            token_seconds=time.perf_counter()-start
            indices=sorted(range(len(order)),key=lambda i:(len(encoded['input_ids'][i]),i))
            scores=[None]*len(order);forward=0.;padded_tokens=0
            for begin in range(0,len(indices),batch_size):
                take=indices[begin:begin+batch_size]
                batch=tokenizer.pad([{k:v[i] for k,v in encoded.items()} for i in take],padding=True,return_tensors='pt')
                padded_tokens+=batch['input_ids'].numel()
                torch.mps.synchronize();tick=time.perf_counter()
                batch={k:v.to('mps') for k,v in batch.items()}
                values=model(**batch,return_dict=True).logits.reshape(-1).float().cpu().tolist()
                torch.mps.synchronize();forward+=time.perf_counter()-tick
                for i,value in zip(take,values):scores[i]=value
            result=ordered_result(row,scores,chunks)
            result['timing']=dict(total_query_seconds=time.perf_counter()-start,tokenization_seconds=token_seconds,
                                  forward_and_transfer_seconds=forward)
            result['input_stats']=dict(pairs=len(order),truncated_pairs=sum(n>512 for n in lengths),
                original_max_tokens=max(lengths),original_total_tokens=sum(lengths),padded_tokens=padded_tokens)
            stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush()
            if (index+1)%10==0 or index==len(rows)-1:
                meta.update(query_count=index+1,mps_current_bytes=torch.mps.current_allocated_memory(),
                            mps_driver_bytes=torch.mps.driver_allocated_memory(),process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                write_json(meta_path,meta)
                print(json.dumps(dict(event='progress',completed=index+1,total=len(rows),segment_seconds=time.perf_counter()-start_all)),flush=True)
    all_rows=read_jsonl(path)
    meta.update(status='completed',query_count=len(all_rows),predictions_sha256=digest(input_bytes(path)),
        inference_query_seconds=sum(r['timing']['total_query_seconds'] for r in all_rows),
        forward_and_transfer_seconds=sum(r['timing']['forward_and_transfer_seconds'] for r in all_rows),
        tokenization_seconds=sum(r['timing']['tokenization_seconds'] for r in all_rows),
        pairs=sum(r['input_stats']['pairs'] for r in all_rows),truncated_pairs=sum(r['input_stats']['truncated_pairs'] for r in all_rows),
        model_load_included_in_query_seconds=False,whole_service_response_time=False)
    write_json(meta_path,meta)
    print(json.dumps(meta,ensure_ascii=False,indent=2),flush=True)


def summarize(out, summary_dir):
    from civic_link.evaluation.runner import run,expand_rankings,retrieval_metrics
    if summary_dir.exists():raise ValueError('choose new summary directory')
    summary_dir.mkdir(parents=True)
    queries=read_jsonl(QUESTIONS);core=read_jsonl(CORE)
    with connect(DB,True) as db:chunks={r['chunk_id']:dict(r) for r in db.execute('select * from chunks')}
    orders={};per={};summary=dict(subsets={},comparisons={},protocol=read_json((out/'protocol.json')),
        bge_run=read_json((out/'bge.run.json')))
    for subset,questions,qpath in [('all',queries,QUESTIONS),('balanced',core,CORE)]:
        summary['subsets'][subset]={};per[subset]={}
        for method,fmt in [('h3','h3'),('coverage','h3'),('bge','neural')]:
            run(qpath,summary_dir/('evaluation_'+subset+'_'+method),benchmark_version='week7-experiment2-'+subset,
                retrieval_predictions=out/(method+'.jsonl'),retrieval_format=fmt,prediction_queries=QUERIES)
            rows=read_jsonl(out/(method+'.jsonl'))
            converted=expand_rankings(rows,chunks,fmt)
            subset_ids={q['question_id'] for q in questions}
            result,values,_=retrieval_metrics(questions,[r for r in converted if r['query_id'] in subset_ids],set(chunks))
            summary['subsets'][subset][method]=result['retrieval']
            per[subset][method]={r['query_id']:r for r in values}
            if subset=='all':orders[method]={r['query_id']:[h['chunk_id'] for h in r['hits']] for r in converted}
        for before,after in [('h3','coverage'),('coverage','bge'),('h3','bge')]:
            gain=[];loss=[]
            for qid,old in per[subset][before].items():
                diff=per[subset][after][qid]['metrics']['10']['all_evidence']-old['metrics']['10']['all_evidence']
                if diff>0:gain.append(qid)
                elif diff<0:loss.append(qid)
            summary['comparisons'][subset+':'+before+'->'+after]=dict(gained=gain,lost=loss)
    assert all(set(orders['h3'][q])==set(orders['coverage'][q])==set(orders['bge'][q]) for q in orders['h3'])
    cases=[]
    for q in queries:
        if q['expected_action']!='answer':continue
        before=per['all']['coverage'][q['question_id']]['metrics']['10']['all_evidence']
        after=per['all']['bge'][q['question_id']]['metrics']['10']['all_evidence']
        if before==after:continue
        details=[]
        for ev in q['evidence_requirements']:
            ranks={m:min((orders[m][q['question_id']].index(c)+1 for c in ev['acceptable_chunk_ids'] if c in orders[m][q['question_id']]),default=None) for m in orders}
            details.append(dict(quote=ev['quote'],ranks=ranks))
        cases.append(dict(question_id=q['question_id'],question=q['question'],question_type=q['question_type'],
                          change='gain' if after>before else 'loss',evidence=details))
    write_jsonl(summary_dir/'changed_questions.jsonl',cases)
    # Source fingerprints and original timings are retained; local paths are not shared.
    write_json(summary_dir/'summary.json',summary)
    write_json(summary_dir/'per_question.json',{subset:values for subset,values in per.items()})
    print(json.dumps({s:{m:r['overall'] for m,r in methods.items()} for s,methods in summary['subsets'].items()},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--output-dir',type=Path,required=True)
    a=sub.add_parser('infer');a.add_argument('--output-dir',type=Path,required=True);a.add_argument('--model-dir',type=Path,required=True)
    a.add_argument('--revision',required=True);a.add_argument('--name',default='bge');a.add_argument('--limit',type=int)
    a.add_argument('--dtype',choices=('float16','float32'),default='float16');a.add_argument('--batch-size',type=int,default=32)
    a=sub.add_parser('summarize');a.add_argument('--output-dir',type=Path,required=True);a.add_argument('--summary-dir',type=Path,required=True)
    args=p.parse_args()
    if args.command=='prepare':prepare(args.output_dir)
    elif args.command=='infer':infer(args.output_dir,args.model_dir,args.revision,args.name,args.limit,args.dtype,args.batch_size)
    else:summarize(args.output_dir,args.summary_dir)
