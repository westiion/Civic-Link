import copy
import csv
import math

import pytest

from evaluate_week7_bge_direct import direct_order, export_rankings, read_rankings, validate_pool


def test_raw_score_sort_uses_exact_ties_and_string_chunk_ids():
    scores = {'a': 1., 'z': math.nextafter(1., math.inf), 'c-2': 0., 'c-10': -0.}
    assert direct_order(scores) == ['z', 'a', 'c-10', 'c-2']
    assert direct_order(dict(reversed(list(scores.items())))) == direct_order(scores)


@pytest.mark.parametrize('scores', [{}, {'a': float('nan')}, {'a': float('inf')},
                                    {'a': True}, {'a': '1'}, {1: 1.}])
def test_invalid_scores_fail_closed(scores):
    with pytest.raises(ValueError):
        direct_order(scores)


def test_csv_preserves_raw_values_and_all_candidates_before_cutoff(tmp_path):
    scores = {'q': {f'c-{i:03}': -float(i) for i in range(105)}}
    scores['q']['c-104'] = math.nextafter(1., math.inf)
    path = tmp_path / 'rankings.csv'
    export_rankings(path, scores)
    assert read_rankings(path) == scores
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 105
    assert rows[0]['chunk_id'] == 'c-104'
    assert rows[0]['bge_raw_score'] == repr(scores['q']['c-104'])
    assert rows[-1]['final_rank'] == '105'


@pytest.mark.parametrize('body', [
    'q,a,1.0,1\nq,a,1.0,2\n',
    'q,b,1.0,1\nq,a,1.0,2\n',
    'q,a,nan,1\n',
    'q,a,1.0,2\n',
])
def test_csv_rejects_duplicates_nonfinite_and_wrong_final_ranks(tmp_path, body):
    path = tmp_path / 'bad.csv'
    path.write_text('query_id,chunk_id,bge_raw_score,final_rank\n' + body)
    with pytest.raises(ValueError):
        read_rankings(path)


def pool_fixture():
    candidates = [dict(chunk_id=f'h-{i:03}', hybrid_rank=i, graph_rank=None,
                       graph_excluded_by_cap=False, fusion_rank=101-i) for i in range(1, 101)]
    candidates += [dict(chunk_id='graph-only', hybrid_rank=None, graph_rank=1,
                        graph_excluded_by_cap=False, fusion_rank=101),
                   dict(chunk_id='excluded', hybrid_rank=None, graph_rank=101,
                        graph_excluded_by_cap=True, fusion_rank=None)]
    old = dict(candidates=candidates)
    revised = copy.deepcopy(old)
    order = [f'h-{i:03}' for i in range(100, 0, -1)] + ['graph-only']
    neural = dict(bge_scores=[dict(chunk_id=c, score=1.) for c in order], ranked_chunk_ids=order)
    hybrid = dict(hits=[dict(chunk_id=f'h-{i:03}') for i in range(1, 101)])
    return old, revised, neural, hybrid


def test_union_includes_graph_only_and_discards_capped_graph_candidates():
    scores = validate_pool(*pool_fixture())
    assert len(scores) == 101
    assert direct_order(scores)[0] == 'graph-only'
    assert 'excluded' not in scores


@pytest.mark.parametrize('mutation', ['missing_score', 'duplicate_score', 'changed_hybrid', 'changed_coverage'])
def test_cache_pool_validation_rejects_partial_or_changed_inputs(mutation):
    old, revised, neural, hybrid = pool_fixture()
    if mutation == 'missing_score':
        neural['bge_scores'].pop()
    elif mutation == 'duplicate_score':
        neural['bge_scores'].append(neural['bge_scores'][0])
    elif mutation == 'changed_hybrid':
        hybrid['hits'].reverse()
    else:
        revised['candidates'][-2]['fusion_rank'] = None
    with pytest.raises(ValueError):
        validate_pool(old, revised, neural, hybrid)
