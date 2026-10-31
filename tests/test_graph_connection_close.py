import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from civic_link.graph import build as graph_construct


class ConnectionCloseTests(unittest.TestCase):
    def check_close(self, stale):
        graph = MagicMock()
        graph.graph = {'corpus_fingerprint': 'expected'}
        connection = MagicMock()
        connection.execute.return_value = []
        with patch.object(Path, 'read_text', return_value='{}'), \
             patch.object(graph_construct.nx, 'node_link_graph', return_value=graph), \
             patch.object(graph_construct, 'connect', return_value=connection), \
             patch.object(graph_construct, 'corpus_identity', return_value='stale' if stale else 'expected'), \
             patch.object(graph_construct, 'validate_graph'):
            if stale:
                with self.assertRaisesRegex(ValueError, 'stale'):
                    graph_construct.load_graph('graph.json', 'temp.sqlite')
            else:
                graph_construct.load_graph('graph.json', 'temp.sqlite')
        connection.close.assert_called_once()

    def test_close_on_success(self):
        self.check_close(False)

    def test_close_on_failure(self):
        self.check_close(True)


if __name__ == '__main__':
    unittest.main()
