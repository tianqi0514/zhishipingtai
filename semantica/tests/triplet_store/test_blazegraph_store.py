import unittest
import os
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from semantica.semantic_extract.triplet_extractor import Triplet
from semantica.triplet_store.blazegraph_store import BlazegraphStore
from semantica.utils.exceptions import ProcessingError


class TestBlazegraphStoreSerialization(unittest.TestCase):
    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_serializes_uri_object(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:knows",
            object="urn:entity:person:2",
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "<urn:entity:person:2>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_serializes_literal_object(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:name",
            object="Jane Doe",
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"Jane Doe\"",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_escapes_literal_object(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:note",
            object='line "one"\\line2',
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"line \\\"one\\\"\\\\line2\"",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_serializes_typed_literal(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:age",
            object="42",
            metadata={"datatype": "http://www.w3.org/2001/XMLSchema#integer"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"42\"^^<http://www.w3.org/2001/XMLSchema#integer>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_build_insert_data_uses_formatter(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:name",
            object="Jane Doe",
        )
        with patch.object(store, "_format_object_for_sparql", return_value="\"Jane Doe\"") as mock_fmt:
            insert_data = store._build_insert_data([triplet])
            mock_fmt.assert_called_once_with(triplet)
            self.assertEqual(
                insert_data,
                "<urn:entity:person:1> <urn:property:name> \"Jane Doe\" .",
            )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_serializes_language_literal(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:label",
            object="Color",
            metadata={"lang": "en"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(obj, "\"Color\"@en")

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_does_not_treat_invalid_uri_like_text_as_uri(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:note",
            object="http not a uri",
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(obj, "\"http not a uri\"")

    # --- Bug 1: prefixed datatype expansion ---

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_expands_xsd_prefix_to_full_iri(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:age",
            object="42",
            metadata={"datatype": "xsd:integer"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"42\"^^<http://www.w3.org/2001/XMLSchema#integer>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_expands_rdf_prefix_to_full_iri(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:value",
            object="hello",
            metadata={"datatype": "rdf:langString"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"hello\"^^<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_rejects_unknown_prefix(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:value",
            object="hello",
            metadata={"datatype": "myns:customType"},
        )
        with self.assertRaises(ValueError):
            store._format_object_for_sparql(triplet)

    # --- Bug 2: metadata injection validation ---

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_rejects_injected_lang_tag(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:label",
            object="Color",
            metadata={"lang": "en . CLEAR ALL #"},
        )
        with self.assertRaises(ValueError):
            store._format_object_for_sparql(triplet)

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_rejects_datatype_with_whitespace(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:age",
            object="42",
            metadata={"datatype": "http://example.org/type CLEAR ALL"},
        )
        with self.assertRaises(ValueError):
            store._format_object_for_sparql(triplet)

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_accepts_full_iri_datatype_no_brackets(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:age",
            object="42",
            metadata={"datatype": "http://www.w3.org/2001/XMLSchema#integer"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"42\"^^<http://www.w3.org/2001/XMLSchema#integer>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_accepts_bracketed_iri_datatype(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:age",
            object="42",
            metadata={"datatype": "<http://www.w3.org/2001/XMLSchema#integer>"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(
            obj,
            "\"42\"^^<http://www.w3.org/2001/XMLSchema#integer>",
        )

    @patch.object(BlazegraphStore, "_connect", autospec=True)
    def test_format_object_accepts_hyphenated_lang_tag(self, _mock_connect):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        triplet = Triplet(
            subject="urn:entity:person:1",
            predicate="urn:property:label",
            object="Colour",
            metadata={"lang": "en-GB"},
        )
        obj = store._format_object_for_sparql(triplet)
        self.assertEqual(obj, "\"Colour\"@en-GB")


from semantica.triplet_store import sparql_escaping


class TestSparqlEscapingExtractionParity(unittest.TestCase):
    """
    Regression tests proving BlazegraphStore._escape_literal /
    BlazegraphStore._resolve_datatype_iri are byte-for-byte identical in
    behavior to the extracted sparql_escaping.escape_literal /
    sparql_escaping.resolve_datatype_iri functions, across every branch of
    both original methods.
    """

    # --- escape_literal: every special character + combinations + plain text ---

    ESCAPE_LITERAL_CASES = [
        "",  # empty string
        "plain text, no special chars",
        "back\\slash",  # backslash
        'embedded "double" quote',  # double quote
        "line1\nline2",  # newline
        "line1\rline2",  # carriage return
        "tab\there",  # tab
        "\\\"\n\r\t",  # all five special characters combined
        'mix \\ and " and \n and \r and \t together',
        42,  # non-str input (both methods call str(value) first)
        None,
    ]

    def test_escape_literal_matches_blazegraph_store_for_every_branch(self):
        with patch.object(BlazegraphStore, "_connect", autospec=True):
            store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        for value in self.ESCAPE_LITERAL_CASES:
            with self.subTest(value=value):
                self.assertEqual(
                    store._escape_literal(value),
                    sparql_escaping.escape_literal(value),
                )

    # --- resolve_datatype_iri: every branch (bracketed, full IRI, prefixed,
    # invalid bracketed, invalid full IRI-with-whitespace, unknown prefix) ---

    RESOLVE_DATATYPE_IRI_VALID_CASES = [
        "<http://www.w3.org/2001/XMLSchema#integer>",  # already bracketed
        "http://www.w3.org/2001/XMLSchema#integer",  # full IRI, no brackets
        "https://example.org/type",  # https scheme
        "urn:isbn:0451450523",  # urn scheme
        "xsd:integer",  # known prefix
        "rdf:langString",  # known prefix
        "rdfs:label",  # known prefix
        "owl:Thing",  # known prefix
        "skos:Concept",  # known prefix
    ]

    RESOLVE_DATATYPE_IRI_ERROR_CASES = [
        "<>",  # empty bracketed IRI
        "<http://example.org/type with space>",  # bracketed IRI with whitespace
        "<http://example.org/type<injected>",  # bracketed IRI with disallowed char
        "http://example.org/type CLEAR ALL",  # full IRI with whitespace
        "myns:customType",  # unknown prefix
        "not_a_uri_no_colon",  # no scheme, no colon-prefixed form matches
        "javascript:alert(1)",  # disallowed scheme, not http/https/urn, no known prefix match
    ]

    def test_resolve_datatype_iri_matches_blazegraph_store_for_valid_cases(self):
        with patch.object(BlazegraphStore, "_connect", autospec=True):
            store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        for datatype in self.RESOLVE_DATATYPE_IRI_VALID_CASES:
            with self.subTest(datatype=datatype):
                self.assertEqual(
                    store._resolve_datatype_iri(datatype),
                    sparql_escaping.resolve_datatype_iri(datatype),
                )

    def test_resolve_datatype_iri_matches_blazegraph_store_for_error_cases(self):
        with patch.object(BlazegraphStore, "_connect", autospec=True):
            store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        for datatype in self.RESOLVE_DATATYPE_IRI_ERROR_CASES:
            with self.subTest(datatype=datatype):
                store_exc = None
                shared_exc = None
                try:
                    store._resolve_datatype_iri(datatype)
                except ValueError as exc:
                    store_exc = exc
                try:
                    sparql_escaping.resolve_datatype_iri(datatype)
                except ValueError as exc:
                    shared_exc = exc

                self.assertIsNotNone(store_exc, f"Expected ValueError from store for {datatype!r}")
                self.assertIsNotNone(shared_exc, f"Expected ValueError from shared module for {datatype!r}")
                self.assertEqual(str(store_exc), str(shared_exc))


def _make_connected_store() -> BlazegraphStore:
    """Create a BlazegraphStore instance bypassing the real _connect() call,
    with .connected forced True (mirrors the state execute_sparql requires)."""
    with patch.object(BlazegraphStore, "_connect", autospec=True):
        store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
    store.connected = True
    return store


class TestBlazegraphStoreConstructExtension(unittest.TestCase):
    """
    Tests for the Blazegraph CONSTRUCT extension: _is_construct_query
    detection, execute_sparql's CONSTRUCT branch (Accept header, Turtle
    parsing via rdflib, triples shape, ProcessingError on malformed Turtle),
    and Property 9 (non-CONSTRUCT queries are byte-for-byte unaffected).
    """

    # --- _is_construct_query detection ---

    def test_is_construct_query_detects_uppercase_keyword(self):
        store = _make_connected_store()
        self.assertTrue(store._is_construct_query("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"))

    def test_is_construct_query_detects_lowercase_keyword(self):
        store = _make_connected_store()
        self.assertTrue(store._is_construct_query("construct { ?s ?p ?o } where { ?s ?p ?o }"))

    def test_is_construct_query_detects_mixed_case_keyword(self):
        store = _make_connected_store()
        self.assertTrue(store._is_construct_query("Construct { ?s ?p ?o } Where { ?s ?p ?o }"))

    def test_is_construct_query_detects_complex_preambles(self):
        # Permanent regression tests covering edge cases discovered during
        # regex stress-testing (issue #7): multiline declarations, empty
        # prefix namespaces, and inline comments embedded in the preamble.
        store = _make_connected_store()
        cases = {
            "multiline_prefix": "PREFIX foaf:\n  <http://xmlns.com/foaf/0.1/>\nCONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            "empty_prefix_namespace": "PREFIX : <http://ex.org/> CONSTRUCT { ?s ?p ?o }",
            "inline_comment": "PREFIX ex: <http://ex.org/>\n# inline comment\nCONSTRUCT { ?s ?p ?o }",
        }
        for name, query in cases.items():
            with self.subTest(case=name):
                self.assertTrue(store._is_construct_query(query))

    def test_is_construct_query_false_for_select(self):
        store = _make_connected_store()
        self.assertFalse(store._is_construct_query("SELECT ?s WHERE { ?s ?p ?o }"))

    def test_is_construct_query_false_for_ask(self):
        store = _make_connected_store()
        self.assertFalse(store._is_construct_query("ASK { ?s ?p ?o }"))

    def test_is_construct_query_does_not_match_substring_inside_identifier(self):
        # "CONSTRUCTOR" contains "CONSTRUCT" as a substring but must not
        # match due to \b word-boundary anchoring.
        store = _make_connected_store()
        self.assertFalse(
            store._is_construct_query("SELECT ?s WHERE { ?s <urn:p> \"CONSTRUCTOR\" }")
        )

    # --- execute_sparql CONSTRUCT path ---

    def test_execute_sparql_construct_sends_turtle_accept_header(self):
        store = _make_connected_store()
        mock_response = MagicMock()
        mock_response.content = (
            b"@prefix ex: <http://ex.org/> .\n"
            b'ex:s1 ex:p1 "value1" .\n'
        )
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response) as mock_post:
            store.execute_sparql("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Accept"], "text/turtle")
        self.assertEqual(
            kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded"
        )

    def test_execute_sparql_construct_parses_triples_from_fixed_turtle_fixture(self):
        store = _make_connected_store()
        turtle_fixture = (
            b"@prefix ex: <http://ex.org/> .\n"
            b'ex:s1 ex:p1 "value1" .\n'
            b"ex:s1 ex:p2 ex:o2 .\n"
        )
        mock_response = MagicMock()
        mock_response.content = turtle_fixture
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response):
            result = store.execute_sparql("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")

        self.assertTrue(result["success"])
        self.assertEqual(result["bindings"], [])
        self.assertEqual(result["variables"], [])
        self.assertEqual(result["metadata"]["result_format"], "construct")

        # "triples" is now a list of (s, p, o, metadata) 4-tuples. Both
        # triples here are plain untyped literals/URIs, so metadata is {}.
        triples = {(s, p, o) for s, p, o, _metadata in result["triples"]}
        self.assertIn(("http://ex.org/s1", "http://ex.org/p1", "value1"), triples)
        self.assertIn(("http://ex.org/s1", "http://ex.org/p2", "http://ex.org/o2"), triples)
        self.assertEqual(len(result["triples"]), 2)
        for _s, _p, _o, metadata in result["triples"]:
            self.assertEqual(metadata, {})

    def test_execute_sparql_construct_result_format_option_forces_construct_path(self):
        # Even for a query that doesn't literally contain "CONSTRUCT",
        # result_format="construct" should force the Turtle-parsing path.
        store = _make_connected_store()
        mock_response = MagicMock()
        mock_response.content = b'@prefix ex: <http://ex.org/> .\nex:s1 ex:p1 "v" .\n'
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response) as mock_post:
            result = store.execute_sparql("SELECT ?s WHERE { ?s ?p ?o }", result_format="construct")

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Accept"], "text/turtle")
        self.assertIn("triples", result)

    def test_execute_sparql_construct_malformed_turtle_raises_processing_error(self):
        store = _make_connected_store()
        mock_response = MagicMock()
        # Deliberately invalid Turtle syntax.
        mock_response.content = b"this is { not [ valid turtle syntax at all !!!"
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response):
            with self.assertRaises(ProcessingError) as ctx:
                store.execute_sparql("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")

        # Must be a ProcessingError, not a raw rdflib exception leaking out.
        self.assertIsInstance(ctx.exception, ProcessingError)
        self.assertNotIsInstance(ctx.exception, (SyntaxError, ValueError))

    def test_execute_sparql_construct_handles_literal_with_braces_in_valid_turtle(self):
        # Adversarial case implied by the brace-matching bug found in the
        # template-string layer (construct_templates._find_matching_brace):
        # confirm rdflib itself parses a *valid* Turtle literal containing
        # brace characters correctly, since this is a different parsing
        # layer (real Turtle syntax, not our {{param}} template string).
        store = _make_connected_store()
        turtle_fixture = (
            b"@prefix ex: <http://ex.org/> .\n"
            b'ex:s1 ex:p1 "text with { and } braces inside" .\n'
        )
        mock_response = MagicMock()
        mock_response.content = turtle_fixture
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response):
            result = store.execute_sparql("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")

        self.assertEqual(len(result["triples"]), 1)
        subject, predicate, obj, metadata = result["triples"][0]
        self.assertEqual(subject, "http://ex.org/s1")
        self.assertEqual(predicate, "http://ex.org/p1")
        self.assertEqual(obj, "text with { and } braces inside")
        self.assertEqual(metadata, {})

    # --- Property 9: non-CONSTRUCT queries are byte-for-byte unaffected ---

    def test_execute_sparql_select_query_response_shape_unchanged(self):
        store = _make_connected_store()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "head": {"vars": ["s", "p", "o"]},
            "results": {
                "bindings": [
                    {
                        "s": {"type": "uri", "value": "http://ex.org/s1"},
                        "p": {"type": "uri", "value": "http://ex.org/p1"},
                        "o": {"type": "literal", "value": "v1"},
                    }
                ]
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response) as mock_post:
            result = store.execute_sparql("SELECT ?s ?p ?o WHERE { ?s ?p ?o }")

        # Accept header must NOT be sent for a non-CONSTRUCT query — request
        # shape is byte-for-byte identical to pre-CONSTRUCT-extension behavior.
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"], {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertNotIn("Accept", kwargs["headers"])

        # Response shape must be exactly the pre-existing shape: no "triples"
        # key at all (not even an empty list) for a plain SELECT response.
        self.assertEqual(
            result,
            {
                "success": True,
                "bindings": mock_response.json.return_value["results"]["bindings"],
                "variables": ["s", "p", "o"],
                "metadata": {
                    "query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o }",
                    "endpoint": store._get_sparql_endpoint(),
                },
            },
        )
        self.assertNotIn("triples", result)

    def test_execute_sparql_ask_query_uses_bindings_path_not_construct(self):
        store = _make_connected_store()
        mock_response = MagicMock()
        mock_response.json.return_value = {"head": {}, "boolean": True}
        mock_response.raise_for_status = MagicMock()

        with patch("semantica.triplet_store.blazegraph_store.requests.post", return_value=mock_response) as mock_post:
            store.execute_sparql("ASK { ?s ?p ?o }")

        _, kwargs = mock_post.call_args
        self.assertNotIn("Accept", kwargs["headers"])


class TestQueryEngineConstructValidation(unittest.TestCase):
    """
    Confirms QueryEngine._validate_query requires zero changes for CONSTRUCT
    support — CONSTRUCT was already a valid keyword before this feature.
    """

    def test_construct_query_passes_validation_unchanged(self):
        from semantica.triplet_store.query_engine import QueryEngine

        engine = QueryEngine()
        query = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
        self.assertTrue(engine._validate_query(query))


if __name__ == "__main__":
    unittest.main()
