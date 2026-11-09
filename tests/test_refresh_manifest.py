import json
import shutil
import subprocess

import pytest

from refresh_manifest import members


def test_members_cover_historical_reports_and_all_published_experiments(tmp_path):
    if not shutil.which('git'):
        pytest.skip('Git is required for checkout membership validation')
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text(
        '/week4/*\n!/week4/REPORT.md\n'
        '/week7/docs/experiment2_cases/case_details.json\n'
        '/week7/evaluation/experiment2/behavior_cases.json\n',
        encoding='utf-8',
    )
    published = {
        'week4/REPORT.md',
        'week6/REPORT.md',
        'week7/GRAPH_EXPLORATION.md',
        'week7/results/graph_exploration_20261103/traversal/summary.json',
        'week7/docs/experiment2_cases/case_details.json.gz',
        'week7/evaluation/experiment2/behavior_cases.json.gz',
        'week8/REPORT.md',
    }
    excluded = {
        'week7/results/__pycache__/cached.pyc',
        'week4/.DS_Store',
        'week4/local-working-copy.json',
        'week7/docs/experiment2_cases/case_details.json',
        'week7/evaluation/experiment2/behavior_cases.json',
        'outputs/local/summary.json',
        'data/corpus/civic.sqlite',
    }
    for name in published | excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')

    inventory = members(tmp_path)
    assert published <= set(inventory)
    assert not excluded.intersection(inventory)
    assert inventory == sorted(set(inventory))


def test_exported_archive_keeps_declared_membership(tmp_path):
    report = tmp_path / 'week4/REPORT.md'
    report.parent.mkdir()
    report.write_text('published report', encoding='utf-8')
    (report.parent / 'local.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "example"\n', encoding='utf-8')
    (tmp_path / 'manifest.json').write_text(
        json.dumps({'files': {'pyproject.toml': {}, 'week4/REPORT.md': {}}}), encoding='utf-8'
    )
    assert members(tmp_path) == ['pyproject.toml', 'week4/REPORT.md']
