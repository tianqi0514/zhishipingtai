"""
Tests for semantica.triplet_store.query_engine, focused on the
QueryResult.triples field added for CONSTRUCT query support.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from semantica.triplet_store.query_engine import QueryEngine, QueryResult


class TestQueryResultTriplesField(unittest.TestCase):
    def test_query_result_triples_defaults_to_empty_list(self):
        result = QueryResult(bindings=[], variables=[])
        self.assertEqual(result.triples, [])

    def test_query_result_triples_field_is_independent_per_instance(self):
        # default_factory=list must produce a fresh list per instance, not a
        # shared mutable default.
        r1 = QueryResult(bindings=[], variables=[])
        r2 = QueryResult(bindings=[], variables=[])
        r1.triples.append(("s", "p", "o"))
        self.assertEqual(r1.triples, [("s", "p", "o")])
        self.assertEqual(r2.triples, [])

    def test_query_result_accepts_explicit_triples(self):
        triples = [("http://ex.org/s1", "http://ex.org/p1", "v1")]
        result = QueryResult(bindings=[], variables=[], triples=triples)
        self.assertEqual(result.triples, triples)


class FakeConstructBackend:
    """Fake store_backend whose execute_sparql returns a CONSTRUCT-shaped result."""

    supports_named_graphs = True

    def execute_sparql(self, query, **options):
        return {
            "success": True,
            "bindings": [],
            "variables": [],
            "triples": [
                ("http://ex.org/s1", "http://ex.org/p1", "v1"),
                ("http://ex.org/s2", "http://ex.org/p2", "v2"),
            ],
            "metadata": {"query": query, "result_format": "construct"},
        }


class FakeBindingsBackend:
    """Fake store_backend whose execute_sparql returns a plain bindings result
    with no "triples" key at all, matching pre-CONSTRUCT-extension backends."""

    supports_named_graphs = True

    def execute_sparql(self, query, **options):
        return {
            "success": True,
            "bindings": [{"s": {"value": "http://ex.org/s1"}}],
            "variables": ["s"],
            "metadata": {"query": query},
        }


class TestExecuteQueryPopulatesTriples(unittest.TestCase):
    def test_execute_query_populates_triples_from_construct_backend(self):
        engine = QueryEngine(enable_caching=False, enable_optimization=False)
        result = engine.execute_query(
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", FakeConstructBackend()
        )
        self.assertEqual(
            result.triples,
            [
                ("http://ex.org/s1", "http://ex.org/p1", "v1"),
                ("http://ex.org/s2", "http://ex.org/p2", "v2"),
            ],
        )

    def test_execute_query_defaults_triples_to_empty_list_when_backend_omits_key(self):
        engine = QueryEngine(enable_caching=False, enable_optimization=False)
        result = engine.execute_query(
            "SELECT ?s WHERE { ?s ?p ?o }", FakeBindingsBackend()
        )
        self.assertEqual(result.triples, [])
        # Non-CONSTRUCT behavior otherwise unaffected.
        self.assertEqual(result.bindings, [{"s": {"value": "http://ex.org/s1"}}])
        self.assertEqual(result.variables, ["s"])


if __name__ == "__main__":
    unittest.main()
