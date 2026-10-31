import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from graph_score_revision import apply, rank, score_candidates
from run_graph_score_revision import split_questions


def candidate(cid, rank=1, coverage=.8, edges=()):
    return dict(chunk_id=cid, fusion_rank=rank, fusion_score=1/rank, rrf_rank=rank,
        hybrid_rank=rank, graph_rank=rank, graph_excluded_by_cap=False,
        rerank_features={'chunk_coverage':coverage}, paths=[dict(graph_hops=len(edges),
            path_edges=[{'relation':e} for e in edges], score=1.,
            score_components={'seed_score':1.,'condition_factor':.95})])


def test_score_magnitude_survives_and_zero_relevance_gets_no_graph_bonus():
    rows=[candidate('a',coverage=.2),candidate('b',2,coverage=.9)]
    assert score_candidates(rows)[0]['chunk_id']=='b'
    assert score_candidates([candidate('a',coverage=0)])[0]['graph_support']==0


def test_seed_score_and_duplicate_paths_cannot_double_count_support():
    rows=[candidate('a'),candidate('b',2)]
    altered=copy.deepcopy(rows)
    altered[0]['paths'][0]['score_components']['seed_score']=.0001
    altered[0]['paths']*=10
    assert score_candidates(rows)==score_candidates(altered)


def test_broad_ancestry_penalty_and_missing_graph():
    rows=[candidate('a',edges=('REFERENCES',)),candidate('b',2,edges=('PART_OF',))]
    scores={c['chunk_id']:c for c in score_candidates(rows)}
    assert scores['b']['graph_support']==pytest.approx(scores['a']['graph_support']*.25)
    rows[0]['graph_excluded_by_cap']=True
    assert next(c for c in score_candidates(rows) if c['chunk_id']=='a')['graph_support']==0


def test_overlap_demotes_duplicate_without_losing_candidates():
    rows=[candidate('a'),candidate('b',2),candidate('c',3,coverage=.79)]
    chunks={c:dict(document_id='doc',start_char=s,end_char=s+100,title=c,text='body')
            for c,s in [('a',0),('b',0),('c',100)]}
    order,_=rank(rows,chunks)
    assert order==['a','c','b']
    result=apply(dict(query_id='test',candidates=rows),chunks)
    assert [h['chunk_id'] for h in result['hits']]==order
    assert rows[0]['fusion_score']==1.
    assert sorted(c['fusion_rank'] for c in result['candidates'])==[1,2,3]


def test_invalid_features_rejected():
    with pytest.raises(ValueError): score_candidates([candidate('a',coverage=float('nan'))])
    with pytest.raises(ValueError): score_candidates([candidate('a'),candidate('a',2)])
    with pytest.raises(ValueError): score_candidates([candidate('a')],weights=(1.,1.,1.))


def test_component_split_is_order_invariant_and_prevents_intent_evidence_leak():
    qs=[dict(question_id='a',intent_component_id='i1',evidence_component_id='e1'),
        dict(question_id='b',intent_component_id='i2',evidence_component_id='e1'),
        dict(question_id='c',intent_component_id='i2',evidence_component_id='e2'),
        dict(question_id='d',intent_component_id='i3',evidence_component_id='e3')]
    split=split_questions(qs)
    assert split==split_questions(list(reversed(qs)))
    assert split['a']==split['b']==split['c']
