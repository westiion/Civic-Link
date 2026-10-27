import copy
from dataclasses import asdict, replace
from types import SimpleNamespace
import pytest
from hybrid_graph import Config, HybridGraph, LEGAL_RELATIONS, ALL_RELATIONS
from compare_extension import validate_pair
from test_hybrid_graph import fixture, ranking
from tokenization import token_set


def test_all_relations_reach_documents_agencies_and_law():
    r = fixture()
    for node, kind, relation in [('doc','Document','REQUIRES'), ('agency','Agency','HANDLED_BY')]:
        r.graph.add_node(node, type=kind, label=node)
        r.graph.add_edge('s',node,key=node,relation=relation)
        r.chunks[node] = dict(chunk_id=node,document_id='d',title=node,text='증명 근거')
        r.chunk_terms[node] = token_set('증명 근거')
        r.evidence['records'][node] = dict(chunk_ids=[node],role='condition',quote='증명 근거',start_char=0,end_char=5,source_document_id='d')
        r.evidence['edge_bindings'][node]=[node]
        r.evidence['node_bindings'][node]=[node]
    r.outgoing={n: sorted([(v,k,e) for _,v,k,e in r.graph.out_edges(n,keys=True,data=True)]) for n in r.graph}
    old=HybridGraph(r,Config(relations=LEGAL_RELATIONS)).search('증명',ranking('c1'))
    new=HybridGraph(r,Config()).search('증명',ranking('c1'))
    assert {c['chunk_id'] for c in new['candidates']} >= {'doc','agency','c4'}
    assert not ({c['chunk_id'] for c in old['candidates']} & {'doc','agency','c4'})
    used={e['relation'] for c in new['candidates'] for path in c['paths'] for e in path['path_edges']}
    assert used==set(ALL_RELATIONS)
    assert all(s['node_id']=='s' for s in new['seeds'])


def pair():
    shared=dict(hybrid_run={},tokenizer='test',tokenizer_package_version='test',score_version='test',
                rerank_weights={},source_sha256={},graph_sha256='g',evidence_sha256='e')
    return dict(shared,config=asdict(Config(relations=LEGAL_RELATIONS))),dict(shared,config=asdict(Config()))


def test_relation_comparison_rejects_confounders():
    a,b=pair();validate_pair(a,b,'relations')
    b['config']['max_hops']=3
    with pytest.raises(ValueError,match='tested policy'):validate_pair(a,b,'relations')
    a,b=pair();b['config']['relations']=('GROUNDED_BY',)
    with pytest.raises(ValueError,match='five'):validate_pair(a,b,'relations')
