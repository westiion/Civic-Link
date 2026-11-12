import json
import re

import pytest

import run_agent
from civic_link.agent.client import QwenClient
from civic_link.agent.protocol import parse_decision
from civic_link.common import ROOT


def test_show_input_is_offline_and_keeps_original_question(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('first-input inspection must not call any model')
    monkeypatch.setattr(run_agent, 'QwenClient', forbidden)
    monkeypatch.setattr(run_agent.LazyTool, 'run', forbidden)
    assert run_agent.main(['--question', '  A와 B를 비교해줘  ', '--show-input']) == 0
    payload = json.loads(capsys.readouterr().out)
    system = json.loads(payload['messages'][0]['content'])
    user = json.loads(payload['messages'][1]['content'])
    assert set(system) == {'역할·데이터 범위', '도구와 관계 설명', '판단 지침', '출력 양식'}
    assert set(user) == {'원래 질문', '현재 실행 상태', '실행 규칙'}
    assert user['원래 질문'] == '  A와 B를 비교해줘  '
    assert system['역할·데이터 범위']['role']
    assert system['역할·데이터 범위']['services']
    assert system['역할·데이터 범위']['source_types']
    assert system['역할·데이터 범위']['scope']
    assert system['판단 지침']
    parse_decision(json.dumps(system['출력 양식']['example']), set())
    tools = system['도구와 관계 설명']
    for name in ('search', 'graph'):
        assert tools[name]['function'] and tools[name]['arguments']
        assert {'id', 'document_id', 'title', 'text', 'source'} <= tools[name]['returns']['evidence_item'].keys()
        assert {'ok', 'no_results', 'error'} <= tools[name]['returns']['status'].keys()
    assert {'ambiguous', 'not_found'} <= tools['graph']['returns']['status'].keys()
    assert tools['graph']['returns']['relation_item']['qualifiers']
    assert tools['result_delivery']['latest_result'] and tools['result_delivery']['evidence']
    assert set(tools['graph']['relations']) == set(tools['graph']['relation_meanings'])
    state = user['현재 실행 상태']
    assert state['evidence'] == state['checks'] == state['history'] == []
    assert state['latest_result'] is None
    assert state['phase'] == 'initial'
    assert user['실행 규칙'] == {'allowed_actions': ['search', 'graph', 'respond'],
                                'remaining_model_calls': 8, 'remaining_tool_calls': 6}
    assert 'expected_action' not in json.dumps(payload)


@pytest.mark.parametrize('json_mode', [False, True])
def test_show_input_matches_http_body_and_uses_runtime_settings(capsys, monkeypatch, json_mode):
    monkeypatch.setenv('QWEN_MODEL', 'environment-model')
    options = ['--question', '  첫 질문  ', '--show-input', '--model', 'chosen-model',
               '--max-output-tokens', '321', '--candidate-k', '13', '--result-k', '4',
               '--graph-direction', 'both', '--graph-max-relations', '3', '--graph-max-evidence', '5',
               '--max-model-calls', '3', '--max-tool-calls', '1']
    if json_mode:
        options += ['--json-mode']
    assert run_agent.main(options) == 0
    preview = json.loads(capsys.readouterr().out)
    system = json.loads(preview['messages'][0]['content'])
    user = json.loads(preview['messages'][1]['content'])
    specs = system['도구와 관계 설명']
    assert (specs['search']['candidate_k'], specs['search']['result_k']) == (13, 4)
    assert (specs['graph']['direction'], specs['graph']['max_relations'], specs['graph']['max_evidence']) == ('both', 3, 5)
    assert user['실행 규칙']['remaining_model_calls'] == 3
    assert user['실행 규칙']['remaining_tool_calls'] == 1
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
    def opener(request, timeout):
        captured.append(json.loads(request.data))
        return Response()
    client = QwenClient('https://example.org/chat', 'chosen-model', max_tokens=321,
                        json_mode=json_mode, opener=opener)
    client.complete(preview['messages'])
    assert captured == [preview]
    assert preview['model'] == 'chosen-model' and preview['max_tokens'] == 321
    assert ('response_format' in preview) == json_mode


def test_published_request_and_readable_view_match_current_first_input(capsys, monkeypatch):
    monkeypatch.setenv('QWEN_MODEL', 'environment-must-not-affect-published-example')
    directory = ROOT/'week8/examples'
    saved = json.loads((directory/'first_qwen_request.json').read_text(encoding='utf-8'))
    question = json.loads(saved['messages'][1]['content'])['원래 질문']
    assert run_agent.main(['--question', question, '--model', 'Qwen3.8-Flash-Next', '--show-input']) == 0
    actual = json.loads(capsys.readouterr().out)
    assert saved == actual
    readable = (directory/'first_qwen_request.md').read_text(encoding='utf-8')
    blocks = [json.loads(text) for text in re.findall(r'```json\n(.*?)\n```', readable, re.S)]
    expected = [{k: v for k, v in actual.items() if k != 'messages'}]
    for message in actual['messages']:
        for section, value in json.loads(message['content']).items():
            assert f'### {section}' in readable
            expected.append(value)
    assert blocks == expected  # Settings and all seven sections, without omitted content.


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
