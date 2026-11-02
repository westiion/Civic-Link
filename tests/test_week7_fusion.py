import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pytest
from run_week7_fusion import fuse


def test_zero_weight_reproduces_bge():
    orders,trace=fuse({'a':3,'b':2,'c':1},{'a':0,'b':.5,'c':1},weight=0)
    assert orders['bge_embedding_rrf']==orders['bge_direct']==['a','b','c']
    assert orders['embedding_only']==['c','b','a']
    assert trace['c']['semantic_contribution']==0


def test_formula_pool_and_deterministic_ties():
    orders,trace=fuse({'a':3,'b':2,'c':1},{'a':0,'b':.5,'c':1})
    assert trace['a']['rrf_score']==1/61+1/63
    assert orders['bge_embedding_rrf'].index('a')<orders['bge_embedding_rrf'].index('c')
    for o in orders.values():assert len(o)==len(set(o))==3 and set(o)=={'a','b','c'}


def test_same_semantic_scores_keep_bge():
    orders,_=fuse({'b':2,'a':1},{'a':.5,'b':.5})
    assert all(o==['b','a'] for o in orders.values())


def test_invalid_cosines_rejected():
    with pytest.raises(ValueError):fuse({'a':1},{'b':.5})
    with pytest.raises(ValueError):fuse({'a':1},{'a':float('nan')})
