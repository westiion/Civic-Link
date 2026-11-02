import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_week7_neighbors import build_index,select


def test_boundaries_and_real_ordinal():
    chunks={c:dict(chunk_id=c,document_id=d,locator=l,ordinal=o,start_char=o*10,end_char=o*10+12)
            for c,d,l,o in [('a','d','제1조',1),('b','d','제1조',2),('c','d','제2조',3),('e','other','제1조',2),('f','d','제1조',4)]}
    graph={'nodes':[dict(id='art',type='Article',source_document_id='d',locator='제1조')]}
    idx=build_index(chunks,graph)
    assert {x['chunk_id'] for x in idx['a']}=={'b','f'}
    assert next(x for x in idx['a'] if x['chunk_id']=='f')['distance']==3
    assert 'c' not in idx and 'e' not in idx


def test_selection_preserves_pool_and_does_not_expand_recursively():
    scores={str(i):20-i for i in range(15)}
    index={'0':[dict(chunk_id='11',distance=1,article_id='art'),dict(chunk_id='12',distance=3,article_id='art')],
           '11':[dict(chunk_id='13',distance=1,article_id='art')]}
    order,trace=select(scores,index,2,True)
    assert order[:8]==list(scores)[:8]
    assert order[8]=='11' and '13' not in order[:10]
    assert len(order)==len(set(order))==len(scores)
    control,_=select(scores,index,2,False)
    assert control[8:10]==['11','12']


def test_missing_neighbor_and_zero_slots_keep_baseline():
    scores={'a':2,'b':1}
    assert select(scores,{},2)[0]==['a','b']
    assert select(scores,{},0)[0]==['a','b']
