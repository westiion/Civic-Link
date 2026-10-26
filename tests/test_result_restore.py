import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restore_results import restore_results


def setup_asset(root):
    (root / 'results').mkdir()
    payload = b'{"question": "test"}\r\n'
    packed = gzip.compress(payload, mtime=0)
    (root / 'results/result.jsonl.gz').write_bytes(packed)
    item = dict(path='results/result.jsonl', archive='results/result.jsonl.gz',
                bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                archive_sha256=hashlib.sha256(packed).hexdigest())
    (root / 'results/compressed_assets.json').write_text(json.dumps({'assets': [item]}), encoding='utf-8')
    return item, payload


def test_restore_exact_bytes_and_idempotence(tmp_path):
    _, payload = setup_asset(tmp_path)
    assert restore_results(tmp_path)['restored_results'] == 1
    assert (tmp_path / 'results/result.jsonl').read_bytes() == payload
    assert restore_results(tmp_path)['restored_results'] == 0


def test_refuses_existing_different_result(tmp_path):
    setup_asset(tmp_path)
    target = tmp_path / 'results/result.jsonl'
    target.write_bytes(b'user work')
    with pytest.raises(ValueError, match='refusing to overwrite'):
        restore_results(tmp_path)
    assert target.read_bytes() == b'user work'


@pytest.mark.parametrize('change', ['checksum', 'size', 'escape'])
def test_invalid_asset_not_restored(tmp_path, change):
    item, _ = setup_asset(tmp_path)
    if change == 'checksum': item['sha256'] = '0' * 64
    elif change == 'size': item['bytes'] = 1
    else: item['path'] = '../outside.jsonl'
    (tmp_path / 'results/compressed_assets.json').write_text(json.dumps({'assets': [item]}), encoding='utf-8')
    with pytest.raises(ValueError): restore_results(tmp_path)
    assert not (tmp_path / 'results/result.jsonl').exists()
    assert not list((tmp_path / 'results').glob('.restore-*'))
