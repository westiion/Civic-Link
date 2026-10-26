import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from compare_extension import candidate_pool, validate_pair
from civic_data import digest, write_json, write_jsonl
from graph_construct import build_graph, load_graph
from hybrid_graph import Config, HybridGraph, load_inputs
from prepare_h2 import compare
from tokenization import TOKENIZER_NAME, token_set
from test_hybrid_graph import fixture, ranking


def test_kiwi_seed_and_uniform_ablation():
    r=fixture();r.graph.nodes['s']['aliases']=['증명'];r.graph.nodes['a']['label']='증명'
    row=HybridGraph(r,Config(seed_mode='lexical',seed_weight='uniform')).search('증명을 발급해요',ranking('c0'))
    assert {s['node_id'] for s in row['seeds']}=={'s','a'}
    assert all(s['score']==1 and s['selection_method']=='lexical' for s in row['seeds'])
    row=HybridGraph(r,Config(seed_weight='uniform')).search('증명을 발급해요',ranking('c0','c1'))
    assert all(s['score']==1 for s in row['seeds'])


def test_h3_preserves_candidates_and_uses_kiwi_idf_for_chunks_and_quotes():
    r=fixture()
    a=HybridGraph(r,Config()).search('증명',ranking('c0','c1'))
    b=HybridGraph(r,Config(rerank='feature')).search('증명',ranking('c0','c1'))
    assert a['seeds']==b['seeds']
    assert candidate_pool(a)==candidate_pool(b)
    assert [h['chunk_id'] for h in a['hits']]==b['rrf_top_chunk_ids']
    bound=next(c for c in b['candidates'] if c['chunk_id']=='c1')
    assert bound['rerank_features']['chunk_coverage']==1
    assert bound['rerank_features']['quote_coverage']==1
    assert any(t.startswith('m:') for t in token_set('증명'))


def test_cap_excluded_candidates_are_not_counted_as_rerank_pool():
    result=HybridGraph(fixture(),Config(graph_k=1)).search('증명',ranking('c0','c1'))
    assert candidate_pool(result)=={'c0','c1'}
    assert 'c3' in {c['chunk_id'] for c in result['candidates']}


def test_no_seed_preserves_hybrid_when_reranking_is_off():
    result=HybridGraph(fixture()).search('관련 없음',ranking('c0','c4'))
    assert [h['chunk_id'] for h in result['hits']]==['c0','c4']
    assert all(h['graph_path'] is None for h in result['hits'])


def provenance(tmp_path, tokenizer=TOKENIZER_NAME):
    queries=tmp_path/'queries.jsonl'; predictions=tmp_path/'hybrid.jsonl'; run=tmp_path/'hybrid.run.json'
    write_jsonl(queries,[dict(query_id='q',question='증명')]);write_jsonl(predictions,[ranking('c0','c1')])
    info=dict(method='hybrid',bm25={'tokenizer':tokenizer},top_k=100,corpus_hash='test',
              queries_sha256=digest(queries.read_bytes()),predictions_sha256=digest(predictions.read_bytes()))
    write_json(run,info)
    return queries,predictions,run


def test_legacy_hybrid_and_modified_inputs_rejected(tmp_path):
    r=fixture();r.graph.graph['corpus_hash']='test'
    paths=provenance(tmp_path,'words+Hangul bigrams')
    with pytest.raises(ValueError,match='Kiwi'):load_inputs(*paths,r.graph,Config())
    paths=provenance(tmp_path)
    assert load_inputs(*paths,r.graph,Config())[0][0]['query_id']=='q'
    paths[0].write_text('{}\n',encoding='utf-8')
    with pytest.raises(ValueError,match='fingerprint'):load_inputs(*paths,r.graph,Config())


def test_comparison_rejects_mixed_hypotheses():
    common=dict(hybrid_run={},tokenizer=TOKENIZER_NAME,tokenizer_package_version='test',score_version='test',
                rerank_weights={},source_sha256={},graph_sha256='g',evidence_sha256='e')
    a=dict(common,config=asdict(Config(seed_mode='lexical',seed_weight='uniform')))
    b=dict(common,config=asdict(Config(seed_weight='uniform')))
    validate_pair(a,b,'h1')
    b['config']['rerank']='feature'
    with pytest.raises(ValueError,match='tested policy'):validate_pair(a,b,'h1')


def test_h2_toggle_keeps_current_legal_reference_repairs():
    db=ROOT/'data/processed/civic.sqlite';registry=ROOT/'data/services.jsonl'
    before,be,bu=build_graph(db,registry,scoped_repairs=False)
    after,ae,au=build_graph(db,registry,scoped_repairs=True)
    additions=compare((before,be),(after,ae))
    assert len(additions)==6 and len(ae['records'])-len(be['records'])==3
    assert bu==au
    assert before.number_of_edges()==after.number_of_edges()==9437
    assert any(e['extraction_rule']=='contextual_same_law_article' for e in be['records'].values())
    assert any('range' in e['extraction_rule'] for e in be['records'].values())
    stored=json.loads((ROOT/'data/processed/graph.evidence.json').read_text(encoding='utf-8'))
    assert ae==stored


def test_hybrid_candidate_export_keeps_top10_evaluation(tmp_path):
    import run_models
    from types import SimpleNamespace
    args=SimpleNamespace(methods=['hybrid'],output_dir=tmp_path/'run',queries=tmp_path/'q.jsonl',questions=tmp_path/'gold.jsonl',
        db=ROOT/'data/processed/civic.sqlite',graph=ROOT/'data/processed/graph.json',top_k=10,candidate_k=100,
        embedding_url='https://no-network.invalid',embedding_model='test',embedding_api_key=None,query_prefix='',document_prefix='',
        embedding_timeout=1,embedding_format='plain',query_cache=tmp_path/'cache.jsonl')
    write_jsonl(args.queries,[dict(query_id='q',question='증명')]);write_jsonl(args.questions,[dict(question_id='q',question='증명')])
    hits=[dict(query_id='q',retriever='hybrid',rank=i,chunk_id=f'c{i}',document_id='d',score=1/i) for i in range(1,101)]
    seen=[]
    def fake_run(db,queries,method,top_k,candidate_k,client):
        seen.append(top_k)
        return [dict(query_id='q',retriever='hybrid',hits=hits)],dict(method='hybrid',top_k=top_k,bm25={'tokenizer':TOKENIZER_NAME})
    def fake_score(questions,predictions,known):
        assert len(predictions[0]['hits'])==10
        return dict(retrieval={'overall':{}},status_counts={}),[]
    dense=SimpleNamespace(dim=2,docs=[{}],backend='fixture')
    with patch.object(run_models,'parse_args',return_value=args),patch.object(run_models,'Dense',return_value=dense),\
         patch.object(run_models,'run',side_effect=fake_run),patch.object(run_models,'score',side_effect=fake_score),\
         patch('urllib.request.urlopen',side_effect=AssertionError('network not permitted')):
        run_models.main()
    assert seen==[100]
    assert len(json.loads((args.output_dir/'hybrid_candidates.jsonl').read_text(encoding='utf-8'))['hits'])==100
    assert json.loads((args.output_dir/'hybrid.run.json').read_text(encoding='utf-8'))['top_k']==10
    assert json.loads((args.output_dir/'hybrid_candidates.run.json').read_text(encoding='utf-8'))['top_k']==100


def test_offline_week4_pipeline_generates_paired_reports(tmp_path, monkeypatch):
    """Exercise orchestration, serialization and evaluator with synthetic data; no model/corpus search."""
    import sqlite3
    import run_week4
    r=fixture();r.graph.graph.update(corpus_hash='test',corpus_fingerprint='test')
    r.graph.nodes['s']['aliases']=['증명'];r.graph.nodes['a']['label']='증명'
    queries,hybrid,hybrid_run=provenance(tmp_path)
    gold=tmp_path/'questions.jsonl'
    write_jsonl(gold,[dict(question_id='q',question='증명',question_type='legal_grounding',document_hops=1,
        expected_answerability='answerable',evidence_requirements=[dict(acceptable_chunk_ids=['c3'],
            source_alias='fixture',locator='test',source_url='https://example.test')])])
    db=tmp_path/'fixture.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE chunks(chunk_id TEXT)');c.executemany('INSERT INTO chunks VALUES(?)',[(cid,) for cid in r.chunks])
        c.execute('CREATE TABLE metadata(key TEXT,value TEXT)');c.execute('INSERT INTO metadata VALUES(?,?)',('corpus_hash',json.dumps('test')))
    graph=tmp_path/'graph.json';write_json(graph,{});write_json(graph.with_suffix('.evidence.json'),{})
    out=tmp_path/'week4'
    monkeypatch.setattr(run_week4,'load_graph',lambda *args:(r.graph,r.evidence))
    monkeypatch.setattr(run_week4,'GraphRetriever',lambda *args:r)
    monkeypatch.setattr(sys,'argv',['run_week4','--hybrid-results',str(hybrid),'--hybrid-run',str(hybrid_run),
        '--queries',str(queries),'--questions',str(gold),'--db',str(db),'--graph',str(graph),
        '--experiments','baseline','h1','h3','--output-dir',str(out)])
    with patch('urllib.request.urlopen',side_effect=AssertionError('network not permitted')):
        run_week4.main()
    manifest=json.loads((out/'week4_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status']=='completed' and manifest['api_calls']==0
    assert len(manifest['outputs'])==5
    for kind in ['baseline','h1','h3']:
        summary=json.loads((out/'comparisons'/kind/'summary.json').read_text(encoding='utf-8'))
        assert summary['scored_questions']==1
        assert (out/'comparisons'/kind/'report.md').exists()
