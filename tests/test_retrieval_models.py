import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from evaluate import evaluate
from retrieval import BM25, EmbeddingClient, normalize_vector, rrf


def test_bm25_finds_matching_korean_document():
    docs = [
        {'chunk_id': 'c1', 'title': '주민등록표 등본', 'text': '세대의 모든 구성원 기록'},
        {'chunk_id': 'c2', 'title': '토지대장', 'text': '토지 소재와 지목 기록'},
    ]
    assert BM25(docs).search('세대 구성원 등본', 1)[0][0] == 'c1'


def test_rrf_combines_ranks_without_using_raw_scores():
    result = rrf([[('a', 100.0), ('b', 1.0)], [('b', 0.9), ('a', 0.8)]], k=2)
    assert [chunk_id for chunk_id, _ in result] == ['a', 'b']
    assert math.isclose(result[0][1], result[1][1])


def test_embedding_fingerprint_is_deterministic():
    first = EmbeddingClient(
        'https://embedding.example.invalid/v1',
        'Qwen3-VL-Embedding-8B',
        input_format='qwen3-vl',
    )
    second = EmbeddingClient(
        'https://embedding.example.invalid/v1',
        'Qwen3-VL-Embedding-8B',
        input_format='qwen3-vl',
    )
    assert first.fingerprint == second.fingerprint
    assert normalize_vector([3, 4]) == [0.6, 0.8]


def test_evaluation_accepts_current_optional_fields():
    questions = [{
        'question_id': 'q1',
        'question': '무엇을 발급하나요?',
        'question_type': 'service_discovery',
        'document_hops': 0,
        'expected_answerability': 'answerable',
        'evidence_requirements': [{
            'acceptable_chunk_ids': ['c1'],
            'source_alias': 'G001',
            'locator': '기본정보',
            'source_url': 'https://example.test',
        }],
    }]
    predictions = [{'query_id': 'q1', 'retriever': 'bm25', 'hits': [{
        'query_id': 'q1', 'retriever': 'bm25', 'rank': 1,
        'chunk_id': 'c1', 'document_id': 'd1', 'score': 1.0,
    }]}]
    report, cases = evaluate(questions, predictions, {'c1'})
    assert report['retrieval']['overall']['metrics']['1']['all_evidence'] == 1
    assert cases[0]['retrieval_status_at_10'] == 'success'
