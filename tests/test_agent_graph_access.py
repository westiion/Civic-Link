from copy import deepcopy
import json

import networkx as nx
import pytest

from civic_link.agent.graph import GraphTool
from civic_link.agent.protocol import parse_decision, InvalidDecision
from civic_link.agent import RetrievalAgent


@pytest.fixture
def access():
    graph=nx.MultiDiGraph()
    graph.add_node('S',type='Service',label='A 증명서',aliases=['A증명'])
    graph.add_node('S2',type='Service',label='B 증명서')
    graph.add_node('R',type='Document',label='위임장')
    graph.add_node('L',type='Law',label='가상법',source_document_id='DL')
    graph.add_node('A',type='Article',label='제1조',locator='제1조',source_document_id='DL')
    graph.add_node('B',type='Article',label='제2조',locator='제2조',source_document_id='DL')
    graph.add_edge('S','R',key='ER',relation='REQUIRES')
    graph.add_edge('S2','R',key='ER2',relation='REQUIRES')
    graph.add_edge('A','B',key='REF',relation='REFERENCES')
    first='제1조\n제2조를 따른다.\n'
    second='제2조\n① 첫 조건입니다.\n② 둘째 조건입니다.\n③ 마지막 조건입니다.\n'
    docs={'DS':dict(title='A 안내',source_type='service_guide',body='제목\n대리인 위임장\n신청 자격은 누구나 가능합니다.\n',source_url='https://example.org/a'),
          'DO':dict(title='B 안내',source_type='service_guide',body='B만의 비공개 조건',source_url='https://example.org/b'),
          'DL':dict(title='가상법',source_type='law',body=first+second,source_url='https://example.org/law')}
    chunks={}
    def chunk(cid,did,a,b,loc):
        chunks[cid]=dict(document_id=did,title=docs[did]['title'],text=docs[did]['body'][a:b],start_char=a,end_char=b,locator=loc)
    chunk('CS','DS',0,11,'안내')
    chunk('CO','DO',0,len(docs['DO']['body']),'안내')
    chunk('CA','DL',0,len(first),'제1조')
    chunk('CB1','DL',len(first),len(first)+20,'제2조')
    chunk('CB2','DL',len(first)+15,len(first+second),'제2조')
    records={cid:dict(source_document_id=c['document_id'],chunk_ids=[cid]) for cid,c in chunks.items()}
    evidence=dict(records=records,node_bindings={'S':['CS'],'S2':['CO'],'R':['CS','CO'],'A':['CA'],'B':['CB1']},
                  edge_bindings={'ER':['CS'],'ER2':['CO'],'REF':['CA']})
    return GraphTool(graph,evidence,chunks,docs,max_body_chars=18)


def read_all(tool,args):
    pages=[]
    while True:
        page=tool.run(args)
        assert page['status']=='ok',page
        assert sum(len(e['text']) for e in page['evidence'])<=tool.max_body_chars
        pages.append(page)
        if not page['next_cursor']: break
        args={**args,'cursor':page['next_cursor']}
        assert len(pages)<30
    return pages


def test_direct_service_collects_full_guide_and_pages_without_gaps(access):
    pages=read_all(access,dict(mode='direct',target_type='node',target_id='S'))
    rows=[r for p in pages for r in p['evidence']]
    assert ''.join(r['text'] for r in rows)==access.documents['DS']['body']
    assert len({r['id'] for r in rows})==len(rows)
    assert pages[-1]['metadata']['remaining_body_chars']==0
    assert all('B만' not in r['text'] for r in rows)
    for r in rows:
        assert r['text']==access.documents[r['document_id']]['body'][r['start_char']:r['end_char']]


def test_article_includes_unbound_later_chunks_and_law_returns_catalog(access):
    catalog=access.run(dict(mode='direct',target_type='document',target_id='DL'))
    assert catalog['evidence']==[] and catalog['reason']=='select_article'
    assert [r['id'] for r in catalog['article_catalog']]==['A','B']
    pages=read_all(access,dict(mode='direct',target_type='node',target_id='B'))
    text=''.join(e['text'] for p in pages for e in p['evidence'])
    assert text==access.documents['DL']['body'][access.chunks['CB1']['start_char']:]
    assert '마지막' in text


def test_cursor_is_query_bound_and_page_deterministic(access):
    args=dict(mode='direct',target_type='node',target_id='S')
    first=access.run(args)
    cursor=first['next_cursor']
    page={**args,'cursor':cursor}
    assert access.run(page)==access.run(page)
    assert access.run({**page,'target_id':'B'})['status']=='error'
    assert access.run({**page,'cursor':'forged'})['status']=='error'
    assert access.run({**page,'relation':'REQUIRES'})['status']=='error'


def test_shared_node_needs_scope_and_traverse_never_mixes_services(access):
    direct=access.run(dict(mode='direct',target_type='node',target_id='R'))
    assert direct['status']=='ambiguous' and direct['reason']=='select_service_scope'
    result=access.run(dict(mode='traverse',target_type='node',target_id='S',relation='REQUIRES',destination_id='R'))
    assert {r['document_id'] for r in result['evidence']}=={'DS'}
    assert result['relations'][0]['path']==['S','R']
    assert access.run(dict(mode='traverse',target_type='node',target_id='S',relation='REQUIRES',destination_id='B'))['status']=='error'


def test_traversal_pages_cover_source_and_selected_article(access):
    pages=read_all(access,dict(mode='traverse',target_type='node',target_id='A',relation='REFERENCES',destination_id='B'))
    assert ''.join(e['text'] for p in pages for e in p['evidence'])==access.documents['DL']['body']
    assert all(p['relations'][0]['to']=='B' for p in pages)


def test_missing_source_is_distinct_from_a_truncated_page(access):
    access.documents['DS']['body']=''
    result=access.run(dict(mode='direct',target_type='node',target_id='S'))
    assert result['status']=='no_results' and result['reason']=='stored_body_missing'
    assert result['metadata']['missing_source_targets']==['S']
    assert result['next_cursor'] is None


def test_candidates_are_observed_retained_and_sorted_without_top_k(access):
    for i in range(25):
        node=f'X{i:02}'
        access.graph.add_node(node,type='Service',label=f'추가서비스{i}')
        access.evidence['node_bindings'][node]=['CS']
    access._init_access(18)
    initial=access.observe('A 증명서',[],[],[])
    assert [r['id'] for r in initial]==['S']
    observed=access.observe('A 증명서',[dict(id='CS')],initial,[])
    assert len(observed)==27 # S, R, and 25 bound services
    later=access.observe('A 증명서',[],observed,[])
    assert later==observed
    assert later[0]['id']=='S'
    assert all('text' not in r for r in later)


@pytest.mark.parametrize('args',[
    {'target':'A','relation':'REFERENCES'},
    {'mode':'direct','target_type':'node','target_id':'A','relation':'REFERENCES'},
    {'mode':'traverse','target_type':'document','target_id':'DL','relation':'REFERENCES'},
    {'mode':'traverse','target_type':'node','target_id':'A'},
])
def test_qwen_protocol_rejects_old_or_incompatible_graph_inputs(args):
    with pytest.raises(InvalidDecision):
        parse_decision(json.dumps(dict(checks=[],action='graph',arguments=args,reason='read')),set())


def test_controller_provides_candidates_and_preserves_page_ranges(access):
    inputs=[]
    class Client:
        def complete(self,messages):
            state=json.loads(messages[1]['content'])['현재 실행 상태']
            inputs.append(state)
            if state['latest_result'] and state['latest_result']['next_cursor'] is None:
                action='respond';arguments=dict(text='확인한 안내',evidence_ids=[r['id'] for r in state['evidence']])
            else:
                action='graph';arguments=dict(mode='direct',target_type='node',target_id='S')
                if state['latest_result']:
                    arguments['cursor']=state['latest_result']['next_cursor']
            return json.dumps(dict(checks=[],action=action,arguments=arguments,reason='확인'),ensure_ascii=False)
    out=RetrievalAgent(Client(),{'graph':access,'search':object()}).run('A 증명서')
    assert out['status']=='responded'
    assert inputs[0]['graph_candidates'][0]['id']=='S'
    assert len(inputs[-1]['graph_pages'])==out['tool_calls']
    assert {d['id'] for d in inputs[-1]['document_candidates']}=={'DS'}
    assert ''.join(e['text'] for e in out['evidence'])==access.documents['DS']['body']
    assert all('start_char' in e for e in inputs[-1]['evidence'])


def test_controller_rejects_unissued_cursor_before_tool_execution(access):
    replies=iter([
        dict(checks=[],action='graph',arguments=dict(mode='direct',target_type='node',target_id='S',cursor='invented'),reason='read'),
        dict(checks=[],action='respond',arguments=dict(text='추가 확인 필요',evidence_ids=[]),reason='stop')])
    class Client:
        def complete(self,messages): return json.dumps(next(replies))
    out=RetrievalAgent(Client(),{'graph':access,'search':object()}).run('A 증명서')
    assert out['tool_calls']==0 and out['status']=='responded'
    assert any(e['stage']=='request_rejected' for e in out['events'])
