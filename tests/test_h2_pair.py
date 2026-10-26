import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_h2 import compare
import networkx as nx


class PairValidationTests(unittest.TestCase):
    def fixture(self):
        g=nx.MultiDiGraph()
        g.add_node('s',type='Service')
        ev=dict(records={'old':{'extraction_rule':'original'}},node_bindings={'s':['old']},edge_bindings={})
        after=copy.deepcopy(ev)
        after['records']['new']={'extraction_rule':'scoped_negative_statement'}
        after['node_bindings']['s'].append('new')
        return g,ev,after

    def test_only_additive_scoped_binding_accepted(self):
        g,b,a=self.fixture()
        diff=compare((g,b),(g,a))
        self.assertEqual(len(diff),1)
        self.assertEqual(diff[0]['owner_id'],'s')

    def test_changes_and_removals_rejected(self):
        for mutation in ('record','binding','node'):
            g,b,a=self.fixture(); other=g.copy()
            if mutation=='record': a['records']['old']['extraction_rule']='changed'
            if mutation=='binding': a['node_bindings']['s'].remove('old')
            if mutation=='node': other.add_node('unexpected')
            with self.assertRaises(ValueError): compare((g,b),(other,a))


if __name__=='__main__': unittest.main()
