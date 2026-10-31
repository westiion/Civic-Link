import gzip
import hashlib
from pathlib import Path
import sys

import pytest

from restore_data import member, restore_asset, sha


def asset(root, *, payload=b'frozen input'):
    source=root/'input.gz'
    source.write_bytes(gzip.compress(payload))
    return dict(archive='input.gz',target='restored/input.jsonl',bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),archive_sha256=sha(source),kind='hybrid')


def test_restore_refuses_conflicting_existing_input(tmp_path):
    item=asset(tmp_path);target=tmp_path/item['target'];target.parent.mkdir();target.write_bytes(b'user edit')
    with pytest.raises(ValueError,match='refusing overwrite'):restore_asset(tmp_path,item)
    assert target.read_bytes()==b'user edit'


def test_restore_rejects_corruption_without_partial_output(tmp_path):
    item=asset(tmp_path);item['sha256']='0'*64
    with pytest.raises(ValueError,match='restored input differs'):restore_asset(tmp_path,item)
    assert not (tmp_path/item['target']).exists()
    assert not list(tmp_path.rglob('.restore-*'))


def test_restore_is_idempotent_and_confined(tmp_path):
    item=asset(tmp_path)
    assert restore_asset(tmp_path,item)
    assert not restore_asset(tmp_path,item)
    with pytest.raises(ValueError,match='unsafe'):member(tmp_path,'../outside')
    link=tmp_path/'linked';link.symlink_to(tmp_path/'restored/input.jsonl')
    with pytest.raises(ValueError,match='unsafe'):member(tmp_path,'linked')
