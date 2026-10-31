import sys
from pathlib import Path
import pytest
from civic_link.reranking.overlap import rerank_overlap, span_overlap


def fixture():
    chunks={cid:dict(document_id=doc,start_char=start,end_char=end) for cid,doc,start,end in [('a','one',0,100),('b','one',0,100),('c','one',100,200),('d','two',0,100)]}
    rows=[dict(chunk_id=cid,fusion_rank=i,fusion_score=score) for i,(cid,score) in enumerate([('a',1),('b',.99),('c',.95),('d',.94)],1)]
    return rows,chunks


def test_redundant_span_yields_to_distinct_evidence_without_dropping_pool():
    rows,chunks=fixture();order,trace=rerank_overlap(rows,chunks,top_k=2)
    assert order==['a','c','b','d'] and set(order)==set(chunks)
    assert span_overlap(chunks['a'],chunks['b'])==1
    assert span_overlap(chunks['a'],chunks['c'])==0
    assert span_overlap(chunks['a'],chunks['d'])==0


def test_zero_penalty_exact_parity_and_gold_fields_do_not_affect_ranking():
    rows,chunks=fixture();expected=rerank_overlap(rows,chunks)[0]
    assert rerank_overlap(rows,chunks,penalty=0)[0]==['a','b','c','d']
    for row in rows:row.update(gold_answer='unrelated',service_ids=['secret'],question_type='changed')
    assert rerank_overlap(rows,chunks)[0]==expected


def test_malformed_scores_are_rejected():
    rows,chunks=fixture();rows[0]['fusion_score']=float('nan')
    with pytest.raises(ValueError,match='invalid score'):rerank_overlap(rows,chunks)
