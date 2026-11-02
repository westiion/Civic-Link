import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pytest
from run_week7_roles import profiles, order
from evaluate_week7_bge_direct import direct_order


def test_profiles_are_scale_invariant_and_match_roles():
    result=profiles([[2,0],[0,3]],[[1,0],[0,1]],.05)
    assert np.allclose(result.sum(axis=1),1)
    assert result[0,0]>.99 and result[1,1]>.99
    assert np.allclose(result,profiles([[1,0],[0,1]],[[5,0],[0,6]],.05))


def test_zero_bonus_reproduces_baseline_and_preserves_pool():
    scores={'a':.7,'b':.7,'c':.1}
    result,_=order(scores,{'a':0,'b':1,'c':1},0)
    assert result==direct_order(scores)
    assert len(result)==len(set(result))==len(scores)


def test_role_bonus_can_recover_close_candidate_but_not_overturn_large_gap():
    scores={'a':1.,'b':.95,'c':0.}
    result,trace=order(scores,{'a':0,'b':1,'c':1},.15)
    assert result==['b','a','c']
    assert trace['b']['role_bonus']==.15


def test_flat_query_is_neutral():
    chunks=profiles([[1,0],[0,1]],[[1,0],[0,1]],.05)
    assert np.allclose(chunks@np.array([.5,.5]),[.5,.5])


def test_invalid_vector_rejected():
    with pytest.raises(ValueError):profiles([[0,0]],[[1,0]],.05)
