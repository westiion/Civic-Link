import json
import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from graph_construct import (  # noqa: E402
    SCOPED_NEGATIVE_STATEMENTS,
    SCOPED_SERVICE_ALIASES,
    build_graph,
    matches_scoped_service_alias,
    scoped_negative_span,
)
from graph_retrieval import GraphRetriever  # noqa: E402


class ScopedRuleTests(unittest.TestCase):
    def test_alias_requires_exact_source_row_category_and_cell_token(self):
        rule = SCOPED_SERVICE_ALIASES[0]
        service = {'service_id': 'SJ-SVC-011', 'name': '가족관계증명서'}
        document = {'alias': 'C01', 'source_type': 'municipal_table'}
        chunk = {
            'locator': '표 1 행 25:',
            'text': '표 1 행 25: 업무: 가족관계등록 | 민원증명: 가족관계, 기본증명, 혼인관계, 입양관계 | 발급수수료 / 관내: 500원',
        }
        self.assertTrue(matches_scoped_service_alias(rule, service, document, chunk))
        for changed in (
            {**document, 'alias': 'OTHER'},
            {**document, 'source_type': 'service_guide'},
        ):
            self.assertFalse(matches_scoped_service_alias(rule, service, changed, chunk))
        for changed in (
            {**chunk, 'locator': '표 1 행 26:'},
            {**chunk, 'text': chunk['text'].replace('가족관계등록', '제적')},
            {**chunk, 'text': chunk['text'].replace('가족관계,', '(폐쇄)가족관계,')},
            {**chunk, 'text': chunk['text'].replace('가족관계,', '가족관계증명서발급신청,')},
        ):
            self.assertFalse(matches_scoped_service_alias(rule, service, document, changed))

    def test_negative_statement_requires_the_exact_reviewed_scope(self):
        rule = SCOPED_NEGATIVE_STATEMENTS[0]
        service = {'service_id': 'SJ-SVC-018', 'name': '장애인증명서'}
        document = {
            'alias': 'G017',
            'source_type': 'service_guide',
            'body': '앞부분\n' + rule['exact_statement'] + '뒷부분\n',
        }
        self.assertEqual(scoped_negative_span(rule, service, document), (4, 4 + len(rule['exact_statement'])))
        self.assertIsNone(scoped_negative_span(rule, service, {**document, 'alias': 'OTHER'}))
        self.assertIsNone(scoped_negative_span(
            rule, service, {**document, 'body': document['body'].replace('국문 증명\n구비서류 없음', '영문 증명\n등록된 정보가 없습니다')}
        ))


class RebuiltGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, cls.evidence, _ = build_graph(
            ROOT / 'data/processed/civic.sqlite', ROOT / 'data/services.jsonl'
        )

    def test_saved_artifacts_match_the_rebuilt_graph(self):
        stored_graph = json.loads((ROOT / 'data/processed/graph.json').read_text(encoding='utf-8'))
        stored_evidence = json.loads((ROOT / 'data/processed/graph.evidence.json').read_text(encoding='utf-8'))
        self.assertEqual(len(self.graph), len(stored_graph['nodes']))
        self.assertEqual(self.graph.number_of_edges(), len(stored_graph['edges']))
        self.assertEqual(self.evidence, stored_evidence)

    def test_registry_fingerprint_is_line_ending_portable(self):
        registry = (ROOT / 'data/services.jsonl').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(self.graph.graph['registry_sha256'], hashlib.sha256(registry).hexdigest())

    def test_scoped_aliases_create_only_the_reviewed_bindings(self):
        records = {
            eid: record for eid, record in self.evidence['records'].items()
            if record['extraction_rule'] == 'scoped_exact_cell_alias'
        }
        self.assertEqual(len(records), 2)

        row_5 = next(eid for eid, record in records.items() if record['source_alias'] == 'C01' and
                     record['quote'].startswith('표 1 행 5:'))
        row_25 = next(eid for eid, record in records.items() if record['source_alias'] == 'C01' and
                      record['quote'].startswith('표 1 행 25:'))
        self.assertIn(row_5, self.evidence['node_bindings']['SJ-SVC-005'])
        for service_id in ('SJ-SVC-011', 'SJ-SVC-012', 'SJ-SVC-013', 'SJ-SVC-014'):
            self.assertIn(row_25, self.evidence['node_bindings'][service_id])

    def test_negative_statement_is_context_not_a_requirement(self):
        records = {
            eid: record for eid, record in self.evidence['records'].items()
            if record['extraction_rule'] == 'scoped_negative_statement'
        }
        self.assertEqual(len(records), 1)
        eid, record = next(iter(records.items()))
        self.assertEqual(record['quote'], SCOPED_NEGATIVE_STATEMENTS[0]['exact_statement'])
        self.assertEqual(record['semantic_context']['condition'], '국문 증명')
        self.assertEqual(record['semantic_context']['statement_polarity'], 'negative')
        self.assertIn(eid, self.evidence['node_bindings']['SJ-SVC-018'])
        self.assertFalse(any(
            edge['relation'] == 'REQUIRES' and '없음' in self.graph.nodes[target].get('label', '')
            for _, target, edge in self.graph.edges(data=True)
        ))

    def test_existing_graph_retriever_collects_seed_evidence_at_zero_hops(self):
        retriever = GraphRetriever(
            self.graph, ROOT / 'data/processed/civic.sqlite', self.evidence
        )
        result = retriever.search('장애인증명서 국문 증명 구비서류', max_hops=0)
        target = 'DOC-30335fdd17932d9272fb-C0003'
        self.assertIn(target, result['candidate_chunk_ids'])
        hit = next(row for row in result['hits'] if row['chunk_id'] == target)
        self.assertEqual(hit['graph_hops'], 0)
        self.assertEqual(hit['evidence_binding']['kind'], 'node')
        self.assertEqual(hit['evidence_binding']['owner_id'], 'SJ-SVC-018')


if __name__ == '__main__':
    unittest.main()
