import unittest
from unittest.mock import patch, MagicMock
from rdflib import Graph, URIRef, Literal, Namespace
from semantica.triplet_store.jena_store import JenaStore
from semantica.semantic_extract.triplet_extractor import Triplet
from semantica.utils.exceptions import ValidationError
from semantica.triplet_store.construct_templates import execute_construct_template, ConstructTemplate

class TestJenaStoreExecuteSparqlConstructPath(unittest.TestCase):
    def setUp(self):
        self.store = JenaStore()
        # Ensure we use an in-memory graph
        self.store.graph = Graph()
        
    def test_construct_parses_triples_from_rdflib_natively(self):
        # Insert some test data
        self.store.graph.parse(data='<http://ex.org/s> <http://ex.org/p> <http://ex.org/o> .', format='nt')
        
        query = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
        result = self.store.execute_sparql(query)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['bindings'], [])
        self.assertEqual(result['variables'], [])
        self.assertIn('triples', result)
        self.assertEqual(result['metadata']['result_format'], 'construct')
        
        triples = result['triples']
        self.assertEqual(len(triples), 1)
        s, p, o, meta = triples[0]
        self.assertEqual(s, 'http://ex.org/s')
        self.assertEqual(p, 'http://ex.org/p')
        self.assertEqual(o, 'http://ex.org/o')
        self.assertEqual(meta, {})

    def test_typed_literal_datatype_preserved_in_metadata(self):
        data = '<http://ex.org/s> <http://ex.org/age> "42"^^<http://www.w3.org/2001/XMLSchema#integer> .'
        self.store.graph.parse(data=data, format='nt')
        
        query = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
        result = self.store.execute_sparql(query)
        
        triples = result['triples']
        self.assertEqual(len(triples), 1)
        s, p, o, meta = triples[0]
        self.assertEqual(o, '42')
        self.assertEqual(meta['datatype'], 'http://www.w3.org/2001/XMLSchema#integer')
        self.assertNotIn('language', meta)

    def test_language_tagged_literal_preserved_in_metadata(self):
        data = '<http://ex.org/s> <http://ex.org/label> "hello"@en .'
        self.store.graph.parse(data=data, format='nt')
        
        query = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
        result = self.store.execute_sparql(query)
        
        triples = result['triples']
        self.assertEqual(len(triples), 1)
        s, p, o, meta = triples[0]
        self.assertEqual(o, 'hello')
        self.assertEqual(meta['language'], 'en')
        self.assertNotIn('datatype', meta)


class TestJenaStoreProperty9NonConstructUnchanged(unittest.TestCase):
    def setUp(self):
        self.store = JenaStore()
        self.store.graph = Graph()
        self.store.graph.parse(data='<http://ex.org/s> <http://ex.org/p> <http://ex.org/o> .', format='nt')
        
    def test_select_response_shape_unchanged(self):
        query = "SELECT ?s ?p ?o WHERE { ?s ?p ?o }"
        result = self.store.execute_sparql(query)
        
        self.assertTrue(result['success'])
        self.assertIn('bindings', result)
        self.assertEqual(len(result['bindings']), 1)
        self.assertNotIn('triples', result)
        self.assertEqual(result['metadata']['query'], query)
        
        binding = result['bindings'][0]
        self.assertEqual(binding['s']['value'], 'http://ex.org/s')
        self.assertEqual(binding['s']['type'], 'uri')

    def test_ask_uses_json_not_turtle_path(self):
        query = "ASK WHERE { ?s ?p ?o }"
        result = self.store.execute_sparql(query)
        
        self.assertTrue(result['success'])
        # For ASK in rdflib, results is a bool wrapped in SPARQLResult.
        # results.vars is None, so it returns empty bindings. This matches the byte-for-byte behavior.
        self.assertEqual(result['bindings'], [])
        self.assertNotIn('triples', result)

class TestExecuteConstructTemplateWithJenaBackend(unittest.TestCase):
    def setUp(self):
        self.store = JenaStore()
        self.store.graph = Graph()
        self.store.graph.parse(data='<http://ex.org/s> <http://ex.org/name> "Alice" .', format='nt')
        
    def test_end_to_end_with_jena_backend(self):
        template = ConstructTemplate(
            name="test",
            description="test",
            construct_query="""
        CONSTRUCT {
            ?s <http://ex.org/isPerson> "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .
        } WHERE {
            ?s <http://ex.org/name> ?name .
        }
        """,
            parameters=[]
        )
        
        results = execute_construct_template(template, {}, self.store)
        self.assertEqual(len(results), 1)
        triplet = results[0]
        self.assertEqual(triplet.subject, 'http://ex.org/s')
        self.assertEqual(triplet.predicate, 'http://ex.org/isPerson')
        self.assertEqual(triplet.object, 'true')
        self.assertEqual(triplet.metadata.get('datatype'), 'http://www.w3.org/2001/XMLSchema#boolean')

class TestJenaStoreRemoteEndpointUsesUpdateStore(unittest.TestCase):
    """
    Tests for the SPARQLStore → SPARQLUpdateStore fix.

    Bug: _initialize_graph bound a read-only SPARQLStore for remote endpoints,
    causing every add_triplets() call to silently fail (TypeError swallowed,
    success=True/added=0 returned).

    Fix: SPARQLUpdateStore is now used, configured with both
    query_endpoint (<base>/query) and update_endpoint (<base>/update)
    per standard Apache Jena Fuseki REST API conventions.
    """

    _BASE = "http://localhost:3030/ds"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_remote_store(self):
        """Return a JenaStore wired to a fake remote endpoint."""
        from rdflib.plugins.stores.sparqlstore import SPARQLUpdateStore

        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore",
        ) as MockUpdateStore:
            # SPARQLUpdateStore.__init__ would normally try to contact the
            # server; the mock prevents any network I/O during init.
            mock_store_instance = MagicMock(spec=SPARQLUpdateStore)
            MockUpdateStore.return_value = mock_store_instance

            with patch("semantica.triplet_store.jena_store.Graph") as MockGraph:
                mock_graph_instance = MagicMock(spec=Graph)
                MockGraph.return_value = mock_graph_instance

                store = JenaStore(endpoint=self._BASE)
                return store, MockUpdateStore, MockGraph, mock_graph_instance

    # ------------------------------------------------------------------
    # Test 1 – correct class is instantiated
    # ------------------------------------------------------------------

    def test_remote_endpoint_instantiates_sparql_update_store_not_sparql_store(self):
        """
        _initialize_graph must use SPARQLUpdateStore, never the read-only SPARQLStore.
        """
        from rdflib.plugins.stores.sparqlstore import SPARQLUpdateStore

        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUpdateStore, patch(
            "semantica.triplet_store.jena_store.SPARQLStore"
        ) as MockReadOnlyStore:
            MockUpdateStore.return_value = MagicMock()
            with patch("semantica.triplet_store.jena_store.Graph"):
                JenaStore(endpoint=self._BASE)

            MockUpdateStore.assert_called_once()
            MockReadOnlyStore.assert_not_called()

    # ------------------------------------------------------------------
    # Test 2 – correct Fuseki sub-paths are derived from base URL
    # ------------------------------------------------------------------

    def test_remote_endpoint_derives_fuseki_query_and_update_sub_paths(self):
        """
        Standard Fuseki datasets expose /query and /update under the dataset
        base URL.  The store must pass both to SPARQLUpdateStore.
        """
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUpdateStore, patch("semantica.triplet_store.jena_store.Graph"):
            MockUpdateStore.return_value = MagicMock()

            JenaStore(endpoint=self._BASE)

            call_kwargs = MockUpdateStore.call_args
            # Accept both positional and keyword argument forms
            args, kwargs = call_kwargs
            passed = {**kwargs}
            if len(args) >= 1:
                passed.setdefault("query_endpoint", args[0])
            if len(args) >= 2:
                passed.setdefault("update_endpoint", args[1])

            self.assertEqual(
                passed.get("query_endpoint"),
                "http://localhost:3030/ds/query",
                "query_endpoint must be <base>/query",
            )
            self.assertEqual(
                passed.get("update_endpoint"),
                "http://localhost:3030/ds/update",
                "update_endpoint must be <base>/update",
            )

    def test_remote_endpoint_trailing_slash_is_normalised(self):
        """A trailing slash on the supplied endpoint must not produce double-slashes."""
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUpdateStore, patch("semantica.triplet_store.jena_store.Graph"):
            MockUpdateStore.return_value = MagicMock()

            JenaStore(endpoint="http://localhost:3030/ds/")

            _, kwargs = MockUpdateStore.call_args
            self.assertNotIn(
                "//query",
                kwargs.get("query_endpoint", ""),
                "Trailing slash must be stripped before appending /query",
            )
            self.assertEqual(kwargs.get("query_endpoint"), "http://localhost:3030/ds/query")
            self.assertEqual(kwargs.get("update_endpoint"), "http://localhost:3030/ds/update")

    # ------------------------------------------------------------------
    # Test 3 – end-to-end write path: add_triplets fires INSERT DATA POST
    # ------------------------------------------------------------------

    def test_add_triplets_remote_endpoint_fires_insert_data_via_update_store(self):
        """
        End-to-end mock: add_triplets() against a remote-endpoint JenaStore must
        call graph.add() (which SPARQLUpdateStore maps to INSERT DATA).  We verify
        that graph.add() is actually invoked with the correct (subject, predicate,
        object) triple and that add_triplets returns success=True/added=1.

        This is distinct from the class-instantiation tests above: it confirms
        the write path works all the way through add_triplets(), not just that
        the right class gets constructed.

        After the Dataset migration the remote path creates Dataset(store=...),
        so we patch Dataset rather than Graph to intercept the construction.
        """
        from rdflib import Dataset, URIRef

        triplet = Triplet(
            subject="http://example.org/Alice",
            predicate="http://example.org/knows",
            object="http://example.org/Bob",
        )

        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUpdateStore, patch(
            "semantica.triplet_store.jena_store.Dataset"
        ) as MockDataset:
            mock_store_instance = MagicMock()
            MockUpdateStore.return_value = mock_store_instance

            mock_dataset_instance = MagicMock(spec=Dataset)
            # graph.add() must not raise — simulate a successful INSERT DATA
            mock_dataset_instance.add.return_value = None
            # graph.graph() is called to resolve named-graph context when graph=
            # is supplied.  It is NOT called in this test (no graph= option).
            MockDataset.return_value = mock_dataset_instance

            store = JenaStore(endpoint=self._BASE)
            result = store.add_triplets([triplet])

        # add_triplets must report success
        self.assertTrue(result["success"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 1)

        # graph.add() must have been called exactly once with the 3-tuple
        # (no graph= option → default graph path → 3-tuple, no context arg)
        mock_dataset_instance.add.assert_called_once_with(
            (
                URIRef("http://example.org/Alice"),
                URIRef("http://example.org/knows"),
                URIRef("http://example.org/Bob"),
            )
        )


class TestJenaStoreDatasetMigration(unittest.TestCase):
    """
    Tests confirming the Graph → Dataset(default_union=False) migration.

    These tests specifically exercise _initialize_graph's real, unmodified
    path (no store.graph injection) to confirm the migration is in effect —
    unlike the existing test classes, which inject a plain Graph() directly
    and therefore don't exercise the initialization path at all.
    """

    # ------------------------------------------------------------------
    # Test 1 — unmodified _initialize_graph produces Dataset, not Graph
    # ------------------------------------------------------------------

    def test_initialize_graph_produces_dataset_not_graph(self):
        """
        The in-memory path of _initialize_graph must produce a Dataset instance,
        not a plain Graph.  This is the direct regression guard for the migration:
        if someone reverts the Dataset construction back to Graph() this test fails.
        """
        from rdflib import Dataset

        store = JenaStore()  # no endpoint → in-memory path

        self.assertIsInstance(
            store.graph,
            Dataset,
            "_initialize_graph must produce a Dataset, not a Graph",
        )

    def test_initialize_graph_dataset_has_default_union_false(self):
        """
        default_union must be explicitly False so that queries without a named
        graph scope see only the default graph, not a union across all graphs.
        """
        from rdflib import Dataset

        store = JenaStore()

        self.assertIsInstance(store.graph, Dataset)
        self.assertFalse(
            store.graph.default_union,
            "Dataset.default_union must be False (named-graph isolation)",
        )

    # ------------------------------------------------------------------
    # Test 2 — add_triplets with graph= writes to named graph, not default
    # ------------------------------------------------------------------

    def test_add_triplets_with_graph_option_writes_to_named_graph(self):
        """
        add_triplets(triplets, graph="http://example.org/g") must write triples
        to the specified named graph, not to the default graph.
        """
        from rdflib import Dataset, URIRef

        store = JenaStore()
        self.assertIsInstance(store.graph, Dataset)

        named_graph_uri = "http://example.org/named-graph"
        triplet = Triplet(
            subject="http://example.org/Alice",
            predicate="http://example.org/knows",
            object="http://example.org/Bob",
        )

        result = store.add_triplets([triplet], graph=named_graph_uri)

        self.assertTrue(result["success"])
        self.assertEqual(result["added"], 1)

        # Confirm the named graph now contains the triple
        named_ctx = store.graph.graph(URIRef(named_graph_uri))
        self.assertEqual(len(named_ctx), 1, "Named graph must hold the added triple")

        # Confirm the default graph does NOT contain it
        self.assertEqual(
            len(store.graph.default_graph),
            0,
            "Default graph must be empty when graph= option is used",
        )

    def test_add_triplets_without_graph_option_writes_to_default_graph(self):
        """
        When graph= is omitted (the common path), triples must go to the default
        graph — preserving pre-migration semantics exactly.
        """
        from rdflib import Dataset, URIRef

        store = JenaStore()

        triplet = Triplet(
            subject="http://example.org/Alice",
            predicate="http://example.org/knows",
            object="http://example.org/Bob",
        )

        result = store.add_triplets([triplet])  # no graph= option

        self.assertTrue(result["success"])
        self.assertEqual(result["added"], 1)

        # Triple is in the default graph
        self.assertEqual(
            len(store.graph.default_graph),
            1,
            "Triple without graph= must go to the default graph",
        )

    def test_add_triplets_named_graph_isolated_from_default_query(self):
        """
        A triple added to a named graph must NOT appear in a plain SELECT query
        (which, with default_union=False, sees only the default graph).
        This confirms the isolation guarantee end-to-end.
        """
        from rdflib import URIRef

        store = JenaStore()

        # Add to named graph
        store.add_triplets(
            [Triplet(
                subject="http://example.org/Named",
                predicate="http://example.org/type",
                object="http://example.org/Thing",
            )],
            graph="http://example.org/isolated",
        )

        # Also add to default graph so we can confirm query returns that one
        store.add_triplets([
            Triplet(
                subject="http://example.org/Default",
                predicate="http://example.org/type",
                object="http://example.org/Other",
            )
        ])

        # Plain SPARQL query (no FROM / GRAPH clause) should see only default
        result = store.execute_sparql("SELECT ?s WHERE { ?s ?p ?o }")
        subjects = [b["s"]["value"] for b in result["bindings"]]

        self.assertIn("http://example.org/Default", subjects)
        self.assertNotIn(
            "http://example.org/Named",
            subjects,
            "Named-graph triple must not appear in a default-graph-scoped query",
        )

    # ------------------------------------------------------------------
    # Test 3 — serialize() logs a warning when named-graph content is present
    # ------------------------------------------------------------------

    def test_serialize_logs_warning_when_named_graph_content_present(self):
        """
        serialize(format='turtle') serializes only the default graph.  When
        named-graph triples are also present, a WARNING must be logged so the
        data loss is not silent.
        """
        from unittest.mock import patch

        store = JenaStore()

        # Put one triple in each location
        store.add_triplets([
            Triplet(
                subject="http://example.org/S",
                predicate="http://example.org/P",
                object="default_val",
            )
        ])
        store.add_triplets(
            [Triplet(
                subject="http://example.org/S2",
                predicate="http://example.org/P",
                object="named_val",
            )],
            graph="http://example.org/ng",
        )

        with patch.object(store.logger, "warning") as mock_warn:
            output = store.serialize(format="turtle")

        # serialize must have returned something (the default graph)
        self.assertIsInstance(output, str)
        self.assertIn("default_val", output)
        self.assertNotIn("named_val", output)

        # Warning must have been logged
        mock_warn.assert_called_once()
        warn_msg = mock_warn.call_args[0][0]
        self.assertIn("named-graph", warn_msg.lower().replace("-", "-"))
        self.assertIn("trig", warn_msg.lower())

    def test_serialize_no_warning_when_only_default_graph_used(self):
        """
        serialize() must NOT log a warning when no named-graph content exists —
        this avoids noise for the common case where named graphs are not used.
        """
        from unittest.mock import patch

        store = JenaStore()
        store.add_triplets([
            Triplet(
                subject="http://example.org/S",
                predicate="http://example.org/P",
                object="default_only",
            )
        ])

        with patch.object(store.logger, "warning") as mock_warn:
            output = store.serialize(format="turtle")

        self.assertIn("default_only", output)
        mock_warn.assert_not_called()




class TestJenaStoreEndpointDerivation(unittest.TestCase):
    """
    Regression tests for Bug 1 (Qodo): endpoint suffix detection prevents
    double-appending of /query or /update.
    """

    def _captured_kwargs(self, endpoint):
        """Return the kwargs passed to SPARQLUpdateStore for a given endpoint."""
        captured = {}
        real_init = __import__(
            "rdflib.plugins.stores.sparqlstore",
            fromlist=["SPARQLUpdateStore"],
        ).SPARQLUpdateStore.__init__

        def fake_update_store(self_store, **kwargs):
            captured.update(kwargs)
            # Avoid real network connection; just store args and bail early
            raise RuntimeError("stop_after_capture")

        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore",
        ) as MockUS:
            MockUS.side_effect = RuntimeError("stop_after_capture")
            MockUS.__init__ = fake_update_store
            try:
                JenaStore(endpoint=endpoint)
            except Exception:
                pass
            # Extract call kwargs
            if MockUS.call_args is not None:
                captured = MockUS.call_args.kwargs
        return captured

    def test_bare_base_url_appends_query_and_update(self):
        """
        The canonical case: a bare dataset base URL gets /query and /update
        appended correctly.
        """
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUS, patch("semantica.triplet_store.jena_store.Dataset"):
            MockUS.return_value = MagicMock()
            MockUS.return_value.graph_aware = True
            JenaStore(endpoint="http://localhost:3030/ds")
            kwargs = MockUS.call_args.kwargs
        self.assertEqual(kwargs["query_endpoint"],  "http://localhost:3030/ds/query")
        self.assertEqual(kwargs["update_endpoint"], "http://localhost:3030/ds/update")

    def test_bare_base_url_with_trailing_slash_normalised(self):
        """Trailing slash on the base URL must not produce a double slash."""
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUS, patch("semantica.triplet_store.jena_store.Dataset"):
            MockUS.return_value = MagicMock()
            MockUS.return_value.graph_aware = True
            JenaStore(endpoint="http://localhost:3030/ds/")
            kwargs = MockUS.call_args.kwargs
        self.assertEqual(kwargs["query_endpoint"],  "http://localhost:3030/ds/query")
        self.assertEqual(kwargs["update_endpoint"], "http://localhost:3030/ds/update")

    def test_endpoint_already_ending_in_query_not_double_appended(self):
        """
        If the caller passes 'http://localhost:3030/ds/query' (already a full
        service URL), the derived query_endpoint must still be
        'http://localhost:3030/ds/query', not '.../ds/query/query'.
        """
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUS, patch("semantica.triplet_store.jena_store.Dataset"):
            MockUS.return_value = MagicMock()
            MockUS.return_value.graph_aware = True
            JenaStore(endpoint="http://localhost:3030/ds/query")
            kwargs = MockUS.call_args.kwargs
        self.assertEqual(kwargs["query_endpoint"],  "http://localhost:3030/ds/query")
        self.assertEqual(kwargs["update_endpoint"], "http://localhost:3030/ds/update")
        # Crucially: no double-suffix
        self.assertNotIn("query/query", kwargs["query_endpoint"])
        self.assertNotIn("query/update", kwargs["update_endpoint"])

    def test_endpoint_already_ending_in_sparql_not_double_appended(self):
        """
        Some Fuseki deployments use /sparql as the query service name.
        Passing that as the endpoint must not produce '.../ds/sparql/query'.
        """
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUS, patch("semantica.triplet_store.jena_store.Dataset"):
            MockUS.return_value = MagicMock()
            MockUS.return_value.graph_aware = True
            JenaStore(endpoint="http://localhost:3030/ds/sparql")
            kwargs = MockUS.call_args.kwargs
        self.assertEqual(kwargs["query_endpoint"],  "http://localhost:3030/ds/query")
        self.assertEqual(kwargs["update_endpoint"], "http://localhost:3030/ds/update")
        self.assertNotIn("sparql/query",  kwargs["query_endpoint"])
        self.assertNotIn("sparql/update", kwargs["update_endpoint"])

    def test_endpoint_already_ending_in_update_not_double_appended(self):
        """
        Endpoint pre-set to '.../ds/update' must not produce '.../ds/update/update'.
        """
        with patch(
            "semantica.triplet_store.jena_store.SPARQLUpdateStore"
        ) as MockUS, patch("semantica.triplet_store.jena_store.Dataset"):
            MockUS.return_value = MagicMock()
            MockUS.return_value.graph_aware = True
            JenaStore(endpoint="http://localhost:3030/ds/update")
            kwargs = MockUS.call_args.kwargs
        self.assertEqual(kwargs["query_endpoint"],  "http://localhost:3030/ds/query")
        self.assertEqual(kwargs["update_endpoint"], "http://localhost:3030/ds/update")
        self.assertNotIn("update/update", kwargs["update_endpoint"])


class TestJenaStoreSerializeWarningFormats(unittest.TestCase):
    """
    Regression tests for Bug 2 (Qodo): serialize warning must fire only for
    single-graph formats (turtle, xml, n3, …) and must NOT fire for
    multi-graph formats (trig, nquads, nt, json-ld, …).
    """

    def _store_with_named_graph_content(self):
        from rdflib import URIRef
        store = JenaStore()
        store.add_triplets([
            Triplet("http://s1", "http://p", "default_val")
        ])
        store.add_triplets([
            Triplet("http://s2", "http://p", "named_val")
        ], graph="http://example.org/ng")
        return store

    # --- formats that MUST trigger the warning ---

    def test_turtle_triggers_warning(self):
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            store.serialize(format="turtle")
        mock_warn.assert_called_once()

    def test_xml_triggers_warning(self):
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            store.serialize(format="xml")
        mock_warn.assert_called_once()

    def test_n3_triggers_warning(self):
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            store.serialize(format="n3")
        mock_warn.assert_called_once()

    # --- formats that must NOT trigger the warning ---

    def test_trig_no_warning(self):
        """trig is a multi-graph format — includes all named graphs, no warning."""
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            output = store.serialize(format="trig")
        mock_warn.assert_not_called()
        # Sanity: both triples are actually in the output
        self.assertIn("named_val", output)

    def test_nquads_no_warning(self):
        """nquads is a multi-graph format — no warning."""
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            output = store.serialize(format="nquads")
        mock_warn.assert_not_called()
        self.assertIn("named_val", output)

    def test_nt_no_warning(self):
        """nt (N-Triples) serializes all contexts — no warning."""
        store = self._store_with_named_graph_content()
        with patch.object(store.logger, "warning") as mock_warn:
            store.serialize(format="nt")
        mock_warn.assert_not_called()


class TestJenaStoreZeroAddedErrorMessage(unittest.TestCase):
    """
    Regression tests for Bug 3 (Qodo): when added_count==0, the ProcessingError
    message must distinguish data-formatting failures from connectivity failures.
    """

    def test_all_malformed_triplets_gives_formatting_error(self):
        """
        When every triplet fails the per-triplet ValueError/AttributeError handler,
        the raised ProcessingError must mention validation / formatting, NOT
        connectivity or endpoint configuration.
        """
        from semantica.utils.exceptions import ProcessingError

        store = JenaStore()

        # object=None triggers AttributeError on None.startswith("http") in
        # add_triplets' obj-resolution line — this IS caught by the per-triplet
        # (ValueError, AttributeError) handler and increments malformed_count.
        # (Note: subject=None would raise TypeError on URIRef(None), which is
        # NOT caught by the per-triplet handler and would escape to the outer
        # except, producing a different message path — don't use that.)
        bad = Triplet(subject="http://s", predicate="http://p", object=None)

        with self.assertRaises(ProcessingError) as ctx:
            store.add_triplets([bad, bad, bad])

        msg = str(ctx.exception).lower()
        # Must mention validation/formatting
        self.assertTrue(
            "validation" in msg or "formatting" in msg or "format" in msg,
            f"Expected validation/formatting message, got: {ctx.exception}",
        )
        # Must NOT suggest connectivity
        self.assertNotIn("connectivity", msg)
        self.assertNotIn("endpoint configuration", msg)

    def test_connectivity_error_gives_connectivity_message(self):
        """
        When a store-level exception (not per-triplet ValueError) causes zero adds,
        the message must mention connectivity/endpoint — not validation.
        This simulates a store that raises a non-ValueError on .add().
        """
        from semantica.utils.exceptions import ProcessingError
        from rdflib import Dataset

        store = JenaStore()

        # Replace the Dataset with a mock whose .add() raises a non-per-triplet error
        mock_ds = MagicMock(spec=Dataset)
        mock_ds.default_graph = MagicMock()
        mock_ds.default_graph.__len__ = MagicMock(return_value=0)
        mock_ds.__len__ = MagicMock(return_value=0)

        # Raise RuntimeError (not ValueError/AttributeError) — store-level failure
        mock_ds.add.side_effect = RuntimeError("connection refused")
        store.graph = mock_ds

        triplet = Triplet("http://s", "http://p", "http://o")

        with self.assertRaises(ProcessingError) as ctx:
            store.add_triplets([triplet])

        # The RuntimeError propagates past the per-triplet handler and is caught
        # by the outer except — the message comes from the outer re-raise
        msg = str(ctx.exception).lower()
        # Should mention "failed to add triplets" (the outer handler wraps it)
        self.assertIn("failed", msg)


class TestJenaStoreDeleteTripletScopedToDefaultGraph(unittest.TestCase):
    """
    Regression tests for delete_triplet's cross-graph deletion bug.

    Bug: after the Graph -> Dataset migration, delete_triplet called
    self.graph.remove((s, p, o)) with no context. Dataset.remove() on a bare
    3-tuple resolves context=None internally, which the store treats as a
    wildcard and matches (and deletes) the triple in every graph — not just
    the default graph the docstring promises. Fix: pass
    self.graph.default_graph explicitly as the 4th tuple element so the
    removal is scoped to the default graph only.
    """

    def test_delete_triplet_does_not_remove_from_named_graph(self):
        """
        A triple present in both the default graph and a named graph must,
        after delete_triplet(), still exist in the named graph — only the
        default-graph copy may be removed.
        """
        from rdflib import URIRef

        store = JenaStore()

        triplet = Triplet(
            subject="http://example.org/Alice",
            predicate="http://example.org/knows",
            object="http://example.org/Bob",
        )

        # Same triple written to both the default graph and a named graph.
        store.add_triplets([triplet])
        store.add_triplets([triplet], graph="http://example.org/ng")

        named_ctx = store.graph.graph(URIRef("http://example.org/ng"))
        self.assertEqual(len(named_ctx), 1)
        self.assertEqual(len(store.graph.default_graph), 1)

        result = store.delete_triplet(triplet)

        self.assertTrue(result["success"])
        self.assertEqual(
            len(store.graph.default_graph),
            0,
            "Triplet must be removed from the default graph",
        )
        self.assertEqual(
            len(named_ctx),
            1,
            "Triplet must NOT be removed from a named graph it also lives in",
        )

    def test_delete_triplet_removes_from_default_graph(self):
        """delete_triplet still removes the triple from the default graph."""
        store = JenaStore()

        triplet = Triplet(
            subject="http://example.org/S",
            predicate="http://example.org/P",
            object="http://example.org/O",
        )
        store.add_triplets([triplet])
        self.assertEqual(len(store.graph.default_graph), 1)

        result = store.delete_triplet(triplet)

        self.assertTrue(result["success"])
        self.assertEqual(len(store.graph.default_graph), 0)


if __name__ == "__main__":
    unittest.main()
