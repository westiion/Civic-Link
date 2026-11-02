"""Resolve archived experiment paths without rewriting hashed run protocols."""
from pathlib import Path
from civic_link.common import ROOT

MOVED = {
    'diversity_h1_20261103': 'h1_diversity',
    'references_h2_20261103': 'h2_references',
    'roles_h3_20261103': 'h3_roles',
    'neighbors_h4_20261103': 'h4_neighbors',
    'fusion_h5_20261103': 'h5_fusion',
}


def resolve_result(path):
    path = Path(path)
    if path.exists():
        return path
    if path.name in MOVED and path.parent.name == 'results' and path.parent.parent.name == 'week7':
        return ROOT/'week7/results/ranking_cases_hypotheses'/MOVED[path.name]
    if (path.name in MOVED.values() and path.parent.name == 'hypotheses'
            and path.parent.parent.name == 'results' and path.parent.parent.parent.name == 'week7'):
        return ROOT/'week7/results/ranking_cases_hypotheses'/path.name
    return path
