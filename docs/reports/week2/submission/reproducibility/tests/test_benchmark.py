import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark import BENCH, select_track, validate, assert_no_lineage
from civic_data import ROOT, read_jsonl
from evaluate import evaluate


class ConversationalBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.qs = read_jsonl(BENCH / 'questions.jsonl')

    def test_ambiguous_first_turn_does_not_force_hidden_gold(self):
        answer = next(q for q in self.qs if q['expected_action'] == 'answer')
        ambiguous = next(q for q in self.qs if q['expected_action'] == 'clarify')
        ids = {cid for q in [answer, ambiguous] for e in q['evidence_requirements'] for cid in e['acceptable_chunk_ids']}
        report, cases = evaluate([answer, ambiguous], [], ids)
        self.assertEqual(report['retrieval']['overall']['question_count'], 1)
        self.assertEqual(cases[1]['retrieval_status_at_10'], 'not_scored_unanswerable')
        self.assertEqual(report['retrieval']['overall']['metrics']['10']['recall'], 0)

    def test_clarification_changes_denominator_only_in_separate_track(self):
        rows = select_track(self.qs, 'clarified')
        ids = {cid for q in rows for e in q['evidence_requirements'] for cid in e['acceptable_chunk_ids']}
        report, _ = evaluate(rows, [], ids)
        self.assertEqual(report['retrieval']['overall']['question_count'], 20)
        self.assertTrue(all(q['expected_answerability'] == 'unanswerable' for q in self.qs if q['clarification']))

    def test_controlled_wrong_evidence_never_claims_official_url(self):
        controlled={q['evidence_condition']:q['provided_evidence'] for q in self.qs if q['task_track']=='provided_evidence_only'}
        for kind in ['conflicting_sources','stale_evidence','wrong_jurisdiction','unresolved_citation']:
            with self.subTest(kind=kind):
                self.assertTrue(all(e['source_url'] is None for e in controlled[kind]))
        truncated=controlled['truncated_exception'][0]['text']
        self.assertIn('일반용',truncated)
        self.assertNotIn('해외이주여권용',truncated)

    def test_standalone_questions_reject_release_lineage(self):
        assert_no_lineage(self.qs)
        with self.assertRaises(ValueError):assert_no_lineage({'parent_question_id':'old'})
        with self.assertRaises(ValueError):assert_no_lineage({'provenance':{'parent_dataset':'old'}})

    def test_live_sources_hashes_and_input_separation(self):
        db = ROOT / 'data/processed/civic.sqlite'
        if not db.exists():
            self.skipTest('local source corpus is required')
        self.assertTrue(validate(db)['valid'])


if __name__ == '__main__':
    unittest.main()
