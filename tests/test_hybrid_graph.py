import sys
import gzip
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import networkx as nx
from hybrid_graph import Config, HybridGraph, LEGAL_RELATIONS
from tokenization import token_set


def fixture():
    g = nx.MultiDiGraph()
    for node, kind in [('s', 'Service'), ('a', 'Article'), ('b', 'Article'), ('law', 'Law')]:
        g.add_node(node, type=kind, label=node)
    for u, v, key, relation in [('s', 'a', 'e1', 'GROUNDED_BY'), ('a', 'b', 'e2', 'REFERENCES'),
                                ('b', 'a', 'e3', 'REFERENCES'), ('a', 'law', 'e4', 'PART_OF')]:
        g.add_edge(u, v, key=key, relation=relation)
    chunks = {cid: dict(chunk_id=cid, document_id='d', title=cid, text='증명 근거')
              for cid in ['c0', 'c1', 'c2', 'c3', 'c4']}
    records = {cid: dict(chunk_ids=[cid], role='service_context' if cid == 'c1' else 'article_context',
                        quote='증명 근거', start_char=0, end_char=5, source_document_id='d') for cid in chunks}
    records['c4']['role'] = 'identity_context'
    ev = dict(records=records, node_bindings={'s': ['c1'], 'a': ['c2'], 'b': ['c3'], 'law': ['c4']},
              edge_bindings={'e1': ['c1'], 'e2': ['c2'], 'e3': ['c3'], 'e4': ['c2']})
    return SimpleNamespace(graph=g, evidence=ev, chunks=chunks, docs={'d': {'source_url': 'https://example.test'}},
        chunk_terms={cid: token_set(ch['title']+' '+ch['text']) for cid, ch in chunks.items()},
        idf={term: 1. for ch in chunks.values() for term in token_set(ch['title']+' '+ch['text'])},
        outgoing={n: sorted([(v, k, e) for _, v, k, e in g.out_edges(n, keys=True, data=True)]) for n in g})


def ranking(*ids):
    return dict(query_id='q', retriever='hybrid', hits=[dict(query_id='q', retriever='hybrid',
        rank=i, chunk_id=cid, document_id='d', score=1 / i) for i, cid in enumerate(ids, 1)])


class ExtensionTests(unittest.TestCase):
    def test_unbound_candidates_preserved_and_two_hop_extension(self):
        result = HybridGraph(fixture(), Config(relations=LEGAL_RELATIONS)).search('증명', ranking('c0', 'c1'))
        hits = {h['chunk_id']: h for h in result['hits']}
        self.assertEqual(hits['c0']['origins'], ['hybrid'])
        self.assertIsNone(hits['c0']['graph_path'])
        self.assertEqual(hits['c3']['graph_path']['graph_hops'], 2)
        self.assertEqual(hits['c3']['origins'], ['graph'])
        self.assertNotIn('c4', hits)
        self.assertEqual(len(hits), len(result['hits']))

    def test_no_seed_preserves_hybrid_order(self):
        result = HybridGraph(fixture()).search('증명', ranking('c0', 'c4'))
        self.assertEqual(result['seeds'], [])
        self.assertEqual([h['chunk_id'] for h in result['hits']], ['c0', 'c4'])

    def test_shared_evidence_cap_and_determinism(self):
        r = fixture()
        r.evidence['node_bindings']['a'].append('c1')
        r.evidence['node_bindings']['b'].append('c1')
        engine = HybridGraph(r, Config(seeds_per_chunk=1))
        first = engine.search('증명', ranking('c1'))
        self.assertEqual(len(first['seeds']), 1)
        self.assertTrue(any(x['reason'] == 'per_chunk_limit' for x in first['seed_decisions']))
        self.assertEqual(first, engine.search('증명', ranking('c1')))

    def test_limits_cycle_and_no_graph_candidate_pruning_of_hybrid(self):
        result = HybridGraph(fixture(), Config(max_edges=1, graph_k=1)).search('증명', ranking('c0', 'c1'))
        self.assertIn('max_edges', result['diagnostics']['truncation_reasons'])
        self.assertIn('c0', [h['chunk_id'] for h in result['hits']])
        result = HybridGraph(fixture(), Config(max_hops=8)).search('증명', ranking('c1'))
        self.assertLess(result['diagnostics']['visited_edges'], 10)

    def test_zero_hop_and_rejected_bad_input(self):
        engine = HybridGraph(fixture(), Config(max_hops=0))
        self.assertEqual([h['chunk_id'] for h in engine.search('증명', ranking('c1'))['hits']], ['c1'])
        for row in [ranking('c1', 'c1'), ranking('missing'), dict(ranking('c1'), retriever='dense')]:
            with self.assertRaises(ValueError):
                engine.search('증명', row)
        with self.assertRaises(ValueError):
            HybridGraph(fixture(), Config(max_nodes=0))

    def test_rrf_uses_ranks_and_keeps_baseline_for_evaluation(self):
        result = HybridGraph(fixture(), Config(relations=LEGAL_RELATIONS)).search('증명', ranking('c0', 'c1'))
        h = next(h for h in result['hits'] if h['chunk_id'] == 'c1')
        self.assertAlmostEqual(h['score'], 1 / 62 + 1 / (60 + h['graph_rank']))
        self.assertEqual([h['chunk_id'] for h in result['baseline_hits']], ['c0', 'c1'])
        self.assertEqual(h['origins'], ['hybrid', 'graph'])

    def test_entry_window_and_node_budget(self):
        result = HybridGraph(fixture(), Config(entry_k=1)).search('증명', ranking('c0', 'c1'))
        self.assertFalse(result['seeds'])
        result = HybridGraph(fixture(), Config(max_nodes=1)).search('증명', ranking('c1'))
        self.assertEqual(result['diagnostics']['visited_nodes'], 1)
        self.assertIn('max_nodes', result['diagnostics']['truncation_reasons'])

    def test_coverage_mode_matches_cached_application_without_neural_models(self):
        from graph_score_revision import apply
        r=fixture()
        for i, chunk in enumerate(r.chunks.values()):
            chunk.update(start_char=i*100,end_char=i*100+50)
        row=ranking('c0','c1')
        cached=HybridGraph(r,Config(rerank='feature')).search('증명',row)
        expected=apply(cached,r.chunks)
        actual=HybridGraph(r,Config(rerank='coverage')).search('증명',row)
        self.assertEqual(actual['hits'],expected['hits'])
        self.assertEqual(actual['candidates'],expected['candidates'])


def test_frozen_gzip_input_preserves_content_provenance(tmp_path):
    from hybrid_graph import load_inputs
    from civic_data import digest
    import pytest
    queries=tmp_path/'queries.jsonl'
    queries.write_text(json.dumps({'query_id':'q','question':'증명'})+'\n')
    plain=tmp_path/'hybrid.jsonl'
    plain.write_text(json.dumps(ranking('c0','c1'))+'\n')
    compressed=tmp_path/'hybrid.jsonl.gz'
    compressed.write_bytes(gzip.compress(plain.read_bytes()))
    metadata=tmp_path/'hybrid.run.json'
    metadata.write_text(json.dumps(dict(method='hybrid',corpus_hash='corpus',
        queries_sha256=digest(queries.read_bytes()),predictions_sha256=digest(plain.read_bytes()),
        top_k=100,bm25={'tokenizer':'kiwi-content+surface-v1'})))
    graph=SimpleNamespace(graph={'corpus_hash':'corpus'})
    assert load_inputs(queries,plain,metadata,graph,Config())==load_inputs(queries,compressed,metadata,graph,Config())
    compressed.write_bytes(gzip.compress(b'changed input'))
    with pytest.raises(ValueError,match='input fingerprint mismatch'):
        load_inputs(queries,compressed,metadata,graph,Config())


if __name__ == '__main__':
    unittest.main()
