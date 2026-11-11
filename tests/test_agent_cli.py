import json

import pytest

import run_agent
from civic_link.common import ROOT


def test_show_input_is_offline_and_keeps_original_question(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('first-input inspection must not call any model')
    monkeypatch.setattr(run_agent, 'QwenClient', forbidden)
    assert run_agent.main(['--question', '  A와 B를 비교해줘  ', '--show-input']) == 0
    payload = json.loads(capsys.readouterr().out)
    state = json.loads(payload['messages'][1]['content'])
    assert state['question'] == '  A와 B를 비교해줘  '
    assert state['evidence'] == state['checks'] == state['history'] == []
    assert state['latest_result'] is None
    assert 'relation_meanings' in payload['messages'][0]['content']
    assert 'expected_action' not in json.dumps(payload)


@pytest.mark.parametrize('options', [
    ['--candidate-k', '0'], ['--candidate-k', '5', '--result-k', '10'],
    ['--max-model-calls', '0'], ['--graph-max-relations', '0'],
    ['--timeout', 'nan'], ['--bge-max-length', '1'],
])
def test_invalid_configuration_fails_before_network(options, monkeypatch):
    monkeypatch.setattr(run_agent, 'QwenClient', lambda *a, **k: pytest.fail('network client constructed'))
    with pytest.raises(SystemExit) as exc:
        run_agent.main(['--question', '질문', '--endpoint', 'https://example.org/chat', *options])
    assert exc.value.code == 2


def test_cli_refuses_existing_output_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(run_agent, 'QwenClient', lambda *a, **k: pytest.fail('network client constructed'))
    with pytest.raises(SystemExit):
        run_agent.main(['--question', '질문', '--output-dir', str(tmp_path)])


def test_index_builder_and_agent_share_encoder_environment(tmp_path, monkeypatch):
    import build_agent_index
    captured = {}
    class Encoder:
        def __init__(self, endpoint, model, **kwargs):
            captured.update(endpoint=endpoint, model=model, **kwargs)
    monkeypatch.setenv('QWEN_EMBEDDING_ENDPOINT', 'https://example.org/embeddings')
    monkeypatch.setenv('QWEN_EMBEDDING_REVISION', 'fixed-test-revision')
    monkeypatch.setenv('QWEN_QUERY_INSTRUCTION', '질문 근거 검색')
    monkeypatch.setattr(build_agent_index, 'QwenEmbeddingClient', Encoder)
    monkeypatch.setattr(build_agent_index, 'build_dense_index', lambda *a, **k: {'built': False})
    monkeypatch.setattr('sys.argv', ['build_agent_index.py', '--output', str(tmp_path/'index.npz')])
    build_agent_index.main()
    runner = run_agent.parser().parse_args(['--question', '질문'])
    assert captured['encoder_revision'] == runner.embedding_revision == 'fixed-test-revision'
    assert captured['query_instruction'] == runner.query_instruction == '질문 근거 검색'
