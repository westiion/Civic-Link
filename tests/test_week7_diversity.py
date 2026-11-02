import argparse
import json
import sqlite3
from contextlib import closing
import numpy as np
import pytest
import run_week7_diversity as d
from civic_link.common import write_json,write_jsonl,digest,input_bytes


def test_source_penalty_and_semantic_penalty_are_separate():
    scores={'a':1.,'b':.95,'c':.9,'d':0.}
    docs={'a':'same','b':'same','c':'other','d':'last'}
    vectors={'a':np.array([1.,0]),'b':np.array([1.,0]),'c':np.array([0.,1]),'d':np.array([-1.,0])}
    assert d.choose(scores,docs,vectors,'bge_direct')[0]==['a','b','c','d']
    for method in d.CONDITIONS[1:]:
        order,trace=d.choose(scores,docs,vectors,method)
        assert order[:2]==['a','c'] and set(order)==set(scores)
        assert len(order)==len(set(order))
    docs['b']='different'
    assert d.choose(scores,docs,vectors,'source_penalty')[0][:2]==['a','b']
    assert d.choose(scores,docs,vectors,'semantic_penalty')[0][:2]==['a','c']


def test_zero_strength_preserves_baseline_and_ties_are_deterministic():
    scores={f'c{i:02}':float(20-i) for i in range(12)}
    docs={c:'same' for c in scores};vectors={c:np.array([1.,0]) for c in scores}
    for method in d.CONDITIONS:
        assert d.choose(scores,docs,vectors,method,strength=0)[0]==list(scores)
    ties={'b':1.,'a':1.}
    assert d.choose(ties,{'a':'x','b':'y'},{'a':np.ones(2),'b':np.ones(2)},'semantic_penalty')[0]==['a','b']


@pytest.mark.parametrize('value',[[],[0.,0.],[float('nan'),1.],[True,1.],'base64'])
def test_bad_embedding_is_rejected(value):
    with pytest.raises(ValueError):d.decode_vector({'data':[{'index':0,'embedding':value}]})


def test_qwen_payload_is_embedding_messages_not_chat_generation():
    p=d.payload('Qwen3-VL-Embedding-8B','text','qwen3-vl')
    assert p['messages'][1]['content']==[{'type':'text','text':'text'}]
    assert 'max_tokens' not in p and 'input' not in p


def test_cache_detects_identity_change_and_corruption(tmp_path):
    path=tmp_path/'vectors.sqlite';rows=[dict(chunk_id='a',input_sha256='hash')]
    with closing(d.cache_open(path,{'model':'m'})) as c:
        blob=np.array([1.,2.],dtype='<f4').tobytes()
        c.execute('insert into vectors values (?,?,?,?,?,?)',('a','hash',2,blob,d.digest(blob),1.));c.commit()
        assert d.read_cache(c,rows)['a'].tolist()==[1.,2.]
        c.execute("update vectors set checksum='corrupt'");c.commit()
        with pytest.raises(ValueError):d.read_cache(c,rows)
    with pytest.raises(ValueError):d.cache_open(path,{'model':'other'})


def test_embed_probe_resume_deduplicates_and_never_calls_chat(tmp_path,monkeypatch):
    rows=[dict(chunk_id='a',text='alpha',input_sha256='ha'),dict(chunk_id='b',text='beta',input_sha256='hb')]
    monkeypatch.setattr(d,'inputs',lambda out:({'inputs_sha256':'fixed'},rows))
    monkeypatch.setenv('CIVIC_EMBEDDING_URL','http://example.invalid/v1')
    monkeypatch.setenv('CIVIC_EMBEDDING_MODEL','Qwen3-VL-Embedding-8B')
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'data':[{'index':0,'embedding':[1.,2.]}]}).encode()
    def request(req,timeout):
        assert req.full_url.endswith('/embeddings')
        calls.append(json.loads(req.data));return Response()
    monkeypatch.setattr(d.urllib.request,'urlopen',request)
    a=argparse.Namespace(out=tmp_path,format='qwen3-vl',limit=1,timeout=10)
    d.embed(a);assert len(calls)==1
    assert json.loads((tmp_path/'embedding.run.json').read_text())['status']=='partial'
    a.limit=None;d.embed(a);assert len(calls)==2
    d.embed(a);assert len(calls)==2
    assert json.loads((tmp_path/'embedding.run.json').read_text())['status']=='completed'
