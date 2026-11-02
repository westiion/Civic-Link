from run_week7_references import select,reference_index


def test_cosine_changes_only_reference_priority_and_keeps_anchor_and_pool():
    scores={f'c{i:02}':20.-i for i in range(13)}
    sims={c:0. for c in scores};sims['c12']=1.
    edges=[dict(target_node='a'+str(i),target_chunks=[f'c{i:02}'],edge_id='e'+str(i)) for i in range(9,13)]
    index={'c00':edges}
    b,_=select(scores,sims,index);s,t=select(scores,sims,index,semantic=True)
    assert b[7]=='c09' and s[7]=='c12'
    assert s[:7]==b[:7]==list(scores)[:7]
    assert set(s)==set(b)==set(scores) and len(s)==len(set(s))
    assert t['selected'][0]['query_cosine']==1.


def test_targets_outside_pool_and_unprotected_anchors_are_not_used():
    scores={f'c{i:02}':20.-i for i in range(12)};sims={c:0. for c in scores}
    edge=dict(target_node='t',target_chunks=['c11','missing'],edge_id='e')
    assert select(scores,sims,{'c08':[edge]},semantic=True)[0]==list(scores)
    order,t=select(scores,sims,{'c00':[edge]},semantic=True)
    assert order[7]=='c11' and len(t['eligible'])==1


def test_one_chunk_per_article_and_already_present_target_skipped():
    scores={f'c{i:02}':20.-i for i in range(12)};sims={c:0. for c in scores}
    edge=dict(target_node='t',target_chunks=['c10','c11'],edge_id='e')
    order,t=select(scores,sims,{'c00':[edge]})
    assert len(t['selected'])==1
    edge['target_chunks'].append('c01')
    assert select(scores,sims,{'c00':[edge]})[0]==list(scores)
    assert select(scores,sims,{},slots=0)[0]==list(scores)


def test_reference_index_requires_outgoing_explicit_evidence():
    graph=dict(nodes=[dict(id='a',type='Article'),dict(id='b',type='Article')],
               edges=[dict(source='a',target='b',relation='REFERENCES',key='e')])
    ev=dict(node_bindings={'b':['target']},edge_bindings={'e':['source']},records={
        'target':dict(role='article_context',chunk_ids=['c2']),
        'source':dict(chunk_ids=['c1'],quote='ref',source_document_id='d',start_char=1,end_char=4)})
    ix=reference_index(graph,ev)
    assert set(ix)=={'c1'} and ix['c1'][0]['target_chunks']==['c2']
    graph['edges'][0]['relation']='PART_OF'
    assert reference_index(graph,ev)=={}
