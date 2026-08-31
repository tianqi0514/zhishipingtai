"""
Tests for semantica.triplet_store.construct_templates.

Covers:
    - ConstructTemplateRegistry: register/get/list/remove + aliases,
      duplicate-name rejection, registration-time validation errors.
    - render_construct_template: parametrized coverage of Correctness
      Properties 1 (literal escaping), 2/3 (URI allowlist + target_graph
      parity), and 4 (typed-literal numeric rendering), per design.md's
      "Parametrized coverage of Correctness Properties 1, 2, 3, 4" section.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import pytest
from rdflib import Graph

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from semantica.triplet_store.construct_templates import (
    ConstructTemplate,
    ConstructTemplateRegistry,
    ParameterDescriptor,
    _find_matching_brace,
    _split_construct_query,
    construct_template_step_handler,
    execute_construct_template,
    render_construct_template,
)
from semantica.utils.exceptions import ProcessingError, ValidationError


def _simple_template(**overrides) -> ConstructTemplate:
    """A minimal valid CONSTRUCT template: one literal param, no target_graph."""
    defaults = dict(
        name="simple_template",
        description="A minimal template for testing.",
        construct_query=(
            "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
            "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
        ),
        parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
    )
    defaults.update(overrides)
    return ConstructTemplate(**defaults)


def _uri_template(**overrides) -> ConstructTemplate:
    """A minimal valid CONSTRUCT template with one "uri"-typed param."""
    defaults = dict(
        name="uri_template",
        description="A minimal template with a uri parameter.",
        construct_query=(
            "CONSTRUCT { {{subject}} <http://ex.org/p1> \"x\" } "
            "WHERE { {{subject}} <http://ex.org/p1> \"x\" }"
        ),
        parameters=[ParameterDescriptor(name="subject", type="uri", required=True)],
    )
    defaults.update(overrides)
    return ConstructTemplate(**defaults)


def _typed_literal_template(datatype: str, **overrides) -> ConstructTemplate:
    """A minimal valid CONSTRUCT template with one "typed-literal" param."""
    defaults = dict(
        name="typed_literal_template",
        description="A minimal template with a typed-literal parameter.",
        construct_query=(
            "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
            "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
        ),
        parameters=[
            ParameterDescriptor(
                name="value", type="typed-literal", required=True, datatype=datatype
            )
        ],
    )
    defaults.update(overrides)
    return ConstructTemplate(**defaults)


# ---------------------------------------------------------------------------
# ConstructTemplateRegistry
# ---------------------------------------------------------------------------


class TestConstructTemplateRegistry(unittest.TestCase):
    def test_register_and_get(self):
        registry = ConstructTemplateRegistry()
        template = _simple_template()
        registry.register(template)
        self.assertIs(registry.get("simple_template"), template)

    def test_register_duplicate_name_raises_and_does_not_overwrite(self):
        registry = ConstructTemplateRegistry()
        t1 = _simple_template(description="original")
        t2 = _simple_template(description="replacement")
        registry.register(t1)
        with self.assertRaises(ValidationError):
            registry.register(t2)
        self.assertIs(registry.get("simple_template"), t1)
        self.assertEqual(registry.get("simple_template").description, "original")

    def test_register_rejects_missing_construct_keyword(self):
        registry = ConstructTemplateRegistry()
        bad_template = _simple_template(
            construct_query="SELECT ?s WHERE { ?s ?p ?o }"
        )
        with self.assertRaises(ValidationError):
            registry.register(bad_template)
        self.assertIsNone(registry.get("simple_template"))

    def test_register_rejects_typed_literal_without_datatype(self):
        registry = ConstructTemplateRegistry()
        bad_template = _simple_template(
            parameters=[ParameterDescriptor(name="value", type="typed-literal", datatype=None)]
        )
        with self.assertRaises(ValidationError):
            registry.register(bad_template)
        self.assertIsNone(registry.get("simple_template"))

    def test_get_miss_returns_none(self):
        registry = ConstructTemplateRegistry()
        self.assertIsNone(registry.get("nonexistent"))

    def test_list_without_category_returns_all_names(self):
        registry = ConstructTemplateRegistry()
        registry.register(_simple_template(name="t1"))
        registry.register(_simple_template(name="t2"))
        self.assertEqual(sorted(registry.list()), ["t1", "t2"])

    def test_list_with_category_filters(self):
        registry = ConstructTemplateRegistry()
        registry.register(_simple_template(name="t1", metadata={"category": "mapping"}))
        registry.register(_simple_template(name="t2", metadata={"category": "other"}))
        registry.register(_simple_template(name="t3", metadata={"category": "mapping"}))
        self.assertEqual(sorted(registry.list(category="mapping")), ["t1", "t3"])
        self.assertEqual(registry.list(category="other"), ["t2"])
        self.assertEqual(registry.list(category="nonexistent_category"), [])

    def test_remove_idempotence(self):
        registry = ConstructTemplateRegistry()
        registry.register(_simple_template())
        self.assertTrue(registry.remove("simple_template"))
        self.assertIsNone(registry.get("simple_template"))
        self.assertFalse(registry.remove("simple_template"))

    def test_register_template_alias_delegates_to_register(self):
        registry = ConstructTemplateRegistry()
        template = _simple_template()
        registry.register_template(template)
        self.assertIs(registry.get("simple_template"), template)
        # Duplicate via alias still raises, same as register().
        with self.assertRaises(ValidationError):
            registry.register_template(_simple_template())

    def test_get_template_alias_delegates_to_get(self):
        registry = ConstructTemplateRegistry()
        template = _simple_template()
        registry.register(template)
        self.assertIs(registry.get_template("simple_template"), template)
        self.assertIsNone(registry.get_template("nonexistent"))

    def test_list_templates_alias_delegates_to_list(self):
        registry = ConstructTemplateRegistry()
        registry.register(_simple_template(name="t1", metadata={"category": "mapping"}))
        registry.register(_simple_template(name="t2"))
        self.assertEqual(sorted(registry.list_templates()), ["t1", "t2"])
        self.assertEqual(registry.list_templates(category="mapping"), ["t1"])

    def test_get_template_info_returns_expected_shape(self):
        registry = ConstructTemplateRegistry()
        template = _simple_template(
            target_graph="http://ex.org/graphs/g1",
            metadata={"category": "mapping"},
        )
        registry.register(template)
        info = registry.get_template_info("simple_template")
        self.assertEqual(
            info,
            {
                "name": "simple_template",
                "description": "A minimal template for testing.",
                "parameter_count": 1,
                "target_graph": "http://ex.org/graphs/g1",
                "metadata": {"category": "mapping"},
            },
        )

    def test_get_template_info_miss_returns_none(self):
        registry = ConstructTemplateRegistry()
        self.assertIsNone(registry.get_template_info("nonexistent"))


# ---------------------------------------------------------------------------
# Property 1: literal escaping
# ---------------------------------------------------------------------------


LITERAL_ESCAPING_CASES = [
    pytest.param("back\\slash", id="backslash"),
    pytest.param('embedded "double" quote', id="double-quote"),
    pytest.param("line1\nline2", id="newline"),
    pytest.param("line1\rline2", id="carriage-return"),
    pytest.param("tab\there", id="tab"),
    pytest.param("\\\"\n\r\t", id="all-combined"),
]


class TestRenderConstructTemplateLiteralEscaping:
    """Property 1: no unescaped injection via literal parameters."""

    @pytest.mark.parametrize("raw_value", LITERAL_ESCAPING_CASES)
    def test_literal_param_round_trips_through_rdflib_turtle_parser(self, raw_value):
        template = _simple_template()
        rendered = render_construct_template(template, params={"value": raw_value})

        # rendered must not contain an unescaped '"' that would terminate the
        # literal early — verified by successfully round-tripping it through
        # rdflib's Turtle parser back to the original string.
        turtle_doc = f"@prefix ex: <http://ex.org/> .\nex:s1 ex:p1 {rendered.split('} WHERE')[0].split('<http://ex.org/p1>')[1].strip().rstrip('}').strip()} ."
        graph = Graph()
        graph.parse(data=turtle_doc, format="turtle")
        literal_values = [str(o) for _, _, o in graph]
        assert raw_value in literal_values

    def test_render_produces_no_unescaped_quote_break(self):
        template = _simple_template()
        rendered = render_construct_template(
            template, params={"value": 'has "quotes" inside'}
        )
        assert '"has \\"quotes\\" inside"' in rendered


# ---------------------------------------------------------------------------
# Property 2/3: URI allowlist + target_graph parity
# ---------------------------------------------------------------------------


BAD_URI_CASES = [
    pytest.param("javascript:alert(1)", id="javascript-scheme"),
    pytest.param("file:///etc/passwd", id="file-scheme"),
    pytest.param("http://example.com/has space", id="embedded-whitespace"),
    pytest.param("http://example.com/<injected>", id="embedded-angle-brackets"),
    pytest.param("http://example.com/{injected}", id="embedded-braces"),
    pytest.param("http://example.com/#frag with space", id="fragment-with-whitespace"),
    pytest.param("", id="empty-string"),
]

GOOD_URI_CASES = [
    pytest.param("http://example.com/ok", id="http-scheme"),
    pytest.param("https://example.com/ok", id="https-scheme"),
    pytest.param("urn:isbn:0451450523", id="urn-scheme"),
]


class TestRenderConstructTemplateUriValidation:
    """Property 2 (URI allowlist enforcement) and Property 3 (target_graph parity)."""

    @pytest.mark.parametrize("bad_uri", BAD_URI_CASES)
    def test_uri_param_rejects_unsafe_values(self, bad_uri):
        template = _uri_template()
        with pytest.raises(ValidationError):
            render_construct_template(template, params={"subject": bad_uri})

    @pytest.mark.parametrize("bad_uri", BAD_URI_CASES)
    def test_target_graph_rejects_same_unsafe_values_as_uri_param(self, bad_uri):
        # A template with a valid "uri" param, but an unsafe target_graph.
        template = _uri_template()
        valid_params = {"subject": "http://example.com/ok"}

        with pytest.raises(ValidationError) as uri_param_exc_info:
            render_construct_template(
                template, params={"subject": bad_uri}
            )
        with pytest.raises(ValidationError) as target_graph_exc_info:
            render_construct_template(
                template, params=valid_params, target_graph=bad_uri
            )

        # Parity check: both failure modes are ValidationError (same type);
        # for non-empty invalid values, the underlying message text is
        # identical because both call validate_uri() on the same bad_uri.
        if bad_uri:
            assert str(uri_param_exc_info.value) == str(target_graph_exc_info.value)

    @pytest.mark.parametrize("good_uri", GOOD_URI_CASES)
    def test_uri_param_accepts_allowed_schemes(self, good_uri):
        template = _uri_template()
        rendered = render_construct_template(template, params={"subject": good_uri})
        assert f"<{good_uri}>" in rendered

    @pytest.mark.parametrize("good_uri", GOOD_URI_CASES)
    def test_target_graph_accepts_same_allowed_schemes_as_uri_param(self, good_uri):
        template = _uri_template()
        rendered = render_construct_template(
            template,
            params={"subject": "http://example.com/ok"},
            target_graph=good_uri,
        )
        assert f"GRAPH <{good_uri}>" in rendered

    def test_template_target_graph_used_when_argument_omitted(self):
        template = _uri_template(target_graph="http://ex.org/graphs/default")
        rendered = render_construct_template(
            template, params={"subject": "http://example.com/ok"}
        )
        assert "GRAPH <http://ex.org/graphs/default>" in rendered

    def test_explicit_target_graph_overrides_template_default(self):
        template = _uri_template(target_graph="http://ex.org/graphs/default")
        rendered = render_construct_template(
            template,
            params={"subject": "http://example.com/ok"},
            target_graph="http://ex.org/graphs/override",
        )
        assert "GRAPH <http://ex.org/graphs/override>" in rendered
        assert "GRAPH <http://ex.org/graphs/default>" not in rendered

    def test_no_target_graph_means_no_graph_wrapping(self):
        template = _uri_template()
        rendered = render_construct_template(
            template, params={"subject": "http://example.com/ok"}
        )
        assert "GRAPH" not in rendered


# ---------------------------------------------------------------------------
# Property 4: typed-literal numeric rendering
# ---------------------------------------------------------------------------


TYPED_LITERAL_CASES = [
    pytest.param("xsd:integer", 42, True, id="xsd-integer"),
    pytest.param("xsd:int", -7, True, id="xsd-int"),
    pytest.param("xsd:long", 9999999999, True, id="xsd-long"),
    pytest.param("xsd:short", 12, True, id="xsd-short"),
    pytest.param("xsd:decimal", 3.14, True, id="xsd-decimal"),
    pytest.param("xsd:double", 2.5e10, True, id="xsd-double"),
    pytest.param("xsd:float", 1.5, True, id="xsd-float"),
    pytest.param("xsd:boolean", True, True, id="xsd-boolean-true"),
    pytest.param("xsd:boolean", False, True, id="xsd-boolean-false"),
    pytest.param("xsd:dateTime", "2024-01-01T00:00:00Z", False, id="xsd-datetime-contrast"),
]


class TestRenderConstructTemplateTypedLiteralRendering:
    """Property 4: typed-literal numeric rendering is unquoted, non-numeric is quoted."""

    @pytest.mark.parametrize("datatype,value,expect_unquoted", TYPED_LITERAL_CASES)
    def test_typed_literal_rendering_by_datatype(self, datatype, value, expect_unquoted):
        template = _typed_literal_template(datatype)
        rendered = render_construct_template(template, params={"value": value})

        # Extract the substituted token from the rendered query.
        placeholder_start = rendered.index("<http://ex.org/p1>") + len("<http://ex.org/p1> ")
        placeholder_end = rendered.index(" }", placeholder_start)
        substituted = rendered[placeholder_start:placeholder_end]

        if expect_unquoted:
            assert not substituted.startswith('"'), (
                f"Expected unquoted rendering for {datatype}, got: {substituted!r}"
            )
        else:
            assert substituted.startswith('"') and "^^<" in substituted, (
                f"Expected quoted ^^<iri> rendering for {datatype}, got: {substituted!r}"
            )

        # rdflib should parse the resulting triple with the expected Literal.
        prefix = "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
        turtle_doc = (
            prefix
            + f"<http://ex.org/s1> <http://ex.org/p1> {substituted} ."
        )
        graph = Graph()
        graph.parse(data=turtle_doc, format="turtle")
        literals = [o for _, _, o in graph]
        assert len(literals) == 1

    def test_typed_literal_rejects_non_numeric_value_for_integer(self):
        template = _typed_literal_template("xsd:integer")
        with pytest.raises(ValidationError):
            render_construct_template(template, params={"value": "not-a-number"})

    def test_typed_literal_missing_datatype_raises(self):
        template = _simple_template(
            parameters=[ParameterDescriptor(name="value", type="typed-literal", datatype=None)]
        )
        with pytest.raises(ValidationError):
            render_construct_template(template, params={"value": 42})


# ---------------------------------------------------------------------------
# Required-parameter completeness (Property 5, spot-checked alongside 1-4)
# ---------------------------------------------------------------------------


class TestRenderConstructTemplateRequiredParameters:
    def test_missing_required_parameter_raises(self):
        template = _simple_template()
        with pytest.raises(ValidationError):
            render_construct_template(template, params={})

    def test_optional_parameter_falls_back_to_default(self):
        template = _simple_template(
            parameters=[
                ParameterDescriptor(name="value", type="literal", required=False, default="fallback")
            ]
        )
        rendered = render_construct_template(template, params={})
        assert '"fallback"' in rendered

    def test_unresolved_placeholder_with_no_descriptor_raises(self):
        template = _simple_template(
            construct_query=(
                "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} ; "
                "<http://ex.org/p2> {{undeclared}} } "
                "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
            )
        )
        with pytest.raises(ValidationError):
            render_construct_template(template, params={"value": "x"})

    def test_unexpected_param_key_raises_and_names_the_key(self):
        # Fix 1: params containing a key with no matching ParameterDescriptor
        # must raise ValidationError naming the unexpected key(s), rather
        # than silently ignoring the extra key.
        template = _simple_template()
        with pytest.raises(ValidationError) as exc_info:
            render_construct_template(
                template, params={"value": "x", "subjcet": "typo-of-subject"}
            )
        assert "subjcet" in str(exc_info.value)

    def test_multiple_unexpected_param_keys_all_named_in_error(self):
        template = _simple_template()
        with pytest.raises(ValidationError) as exc_info:
            render_construct_template(
                template,
                params={"value": "x", "extra_one": 1, "extra_two": 2},
            )
        message = str(exc_info.value)
        assert "extra_one" in message
        assert "extra_two" in message


# ---------------------------------------------------------------------------
# _split_construct_query / _find_matching_brace: CONSTRUCT/WHERE boundary
# parsing. This logic was added to fill a gap left as unspecified stubs
# (extract_construct_clause/extract_where_body) in design.md; it is
# exercised whenever target_graph wrapping is requested.
# ---------------------------------------------------------------------------


class TestSplitConstructQuery(unittest.TestCase):
    """
    Direct coverage of _split_construct_query / _find_matching_brace against
    the 5 scenarios called out for verification, plus a regression case for
    a bug found during that verification (see test 5b).
    """

    def test_1_prefix_declarations_before_construct_are_preserved_as_preamble(self):
        query = (
            "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
            'CONSTRUCT { <http://ex.org/s1> foaf:name "Alice" } '
            'WHERE { <http://ex.org/s1> foaf:name "Alice" }'
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(preamble, "PREFIX foaf: <http://xmlns.com/foaf/0.1/>")
        self.assertEqual(construct_clause, '{ <http://ex.org/s1> foaf:name "Alice" }')
        self.assertEqual(where_body.strip(), '<http://ex.org/s1> foaf:name "Alice"')

    def test_1b_target_graph_wrapping_preserves_prefix_preamble(self):
        # End-to-end: PREFIX preamble must survive graph-wrapping, not just
        # the standalone splitter.
        template = ConstructTemplate(
            name="prefixed_template",
            description="test",
            construct_query=(
                "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
                'CONSTRUCT { <http://ex.org/s1> foaf:name "Alice" } '
                'WHERE { <http://ex.org/s1> foaf:name "Alice" }'
            ),
        )
        rendered = render_construct_template(
            template, params={}, target_graph="http://ex.org/graphs/g1"
        )
        self.assertTrue(rendered.startswith("PREFIX foaf: <http://xmlns.com/foaf/0.1/>"))
        self.assertIn("GRAPH <http://ex.org/graphs/g1>", rendered)

    def test_2_nested_braces_in_construct_clause_blank_node_pattern(self):
        query = (
            "CONSTRUCT { ?s ?p [ <http://ex.org/p2> ?o2 ] } "
            "WHERE { ?s ?p ?o2 }"
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(preamble, "")
        self.assertEqual(construct_clause, "{ ?s ?p [ <http://ex.org/p2> ?o2 ] }")
        self.assertEqual(where_body.strip(), "?s ?p ?o2")

    def test_2b_nested_braces_in_construct_clause_graph_pattern(self):
        # A CONSTRUCT clause containing an actual nested { ... } (not just
        # blank-node [ ... ] syntax), to directly exercise brace-depth
        # tracking rather than square-bracket handling.
        query = (
            "CONSTRUCT { GRAPH <http://ex.org/g1> { ?s ?p ?o } } "
            "WHERE { ?s ?p ?o }"
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(construct_clause, "{ GRAPH <http://ex.org/g1> { ?s ?p ?o } }")
        self.assertEqual(where_body.strip(), "?s ?p ?o")

    def test_3_nested_braces_in_where_clause_optional_block(self):
        query = (
            "CONSTRUCT { ?s <http://ex.org/p1> ?o } "
            "WHERE { ?s <http://ex.org/p1> ?o . OPTIONAL { ?s <http://ex.org/p2> ?o2 } }"
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(construct_clause, "{ ?s <http://ex.org/p1> ?o }")
        self.assertEqual(
            where_body.strip(),
            "?s <http://ex.org/p1> ?o . OPTIONAL { ?s <http://ex.org/p2> ?o2 }",
        )

    def test_3b_nested_braces_in_where_clause_filter_not_exists_block(self):
        query = (
            "CONSTRUCT { ?s <http://ex.org/p1> ?o } "
            "WHERE { ?s <http://ex.org/p1> ?o . "
            "FILTER NOT EXISTS { ?s <http://ex.org/p2> ?o2 } }"
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(construct_clause, "{ ?s <http://ex.org/p1> ?o }")
        self.assertEqual(
            where_body.strip(),
            "?s <http://ex.org/p1> ?o . FILTER NOT EXISTS { ?s <http://ex.org/p2> ?o2 }",
        )

    def test_4_where_keyword_like_substring_inside_construct_clause_literal(self):
        # "WHERE" appearing inside a string literal in the CONSTRUCT clause
        # must not be mistaken for the real WHERE keyword.
        query = (
            'CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> "the WHERE clause matters" } '
            "WHERE { <http://ex.org/s1> <http://ex.org/p1> ?o }"
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(
            construct_clause,
            '{ <http://ex.org/s1> <http://ex.org/p1> "the WHERE clause matters" }',
        )
        self.assertEqual(where_body.strip(), "<http://ex.org/s1> <http://ex.org/p1> ?o")

    def test_4b_brace_character_inside_where_clause_literal_does_not_break_matching(self):
        # A '}' character embedded inside a string literal within the WHERE
        # body must not be counted as a real closing brace by
        # _find_matching_brace (regression test for a bug found during
        # verification: braces inside literals were originally counted
        # regardless of string-literal context, corrupting brace-depth
        # tracking and truncating/duplicating query text).
        query = (
            "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> ?o } "
            'WHERE { <http://ex.org/s1> <http://ex.org/p1> "text with } inside" }'
        )
        preamble, construct_clause, where_body = _split_construct_query(query)
        self.assertEqual(construct_clause, "{ <http://ex.org/s1> <http://ex.org/p1> ?o }")
        self.assertEqual(
            where_body.strip(),
            '<http://ex.org/s1> <http://ex.org/p1> "text with } inside"',
        )

    def test_4c_end_to_end_literal_with_brace_survives_target_graph_wrapping(self):
        # End-to-end regression test: a rendered "literal" parameter value
        # containing a '}' character must survive target_graph wrapping
        # intact, not get truncated by brace-depth confusion.
        template = ConstructTemplate(
            name="brace_in_literal",
            description="test",
            construct_query=(
                "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
                "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
            ),
            parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
        )
        rendered = render_construct_template(
            template,
            params={"value": "text with } inside"},
            target_graph="http://ex.org/graphs/g1",
        )
        self.assertIn('"text with } inside"', rendered)
        # Must appear intact in both the CONSTRUCT clause and the GRAPH-wrapped
        # WHERE body — not truncated at the embedded '}'.
        self.assertEqual(rendered.count('"text with } inside"'), 2)

    def test_5_mismatched_braces_more_open_than_close_raises_validation_error(self):
        query = "CONSTRUCT { ?s ?p ?o WHERE { ?s ?p ?o }"
        with self.assertRaises(ValidationError):
            _split_construct_query(query)

    def test_5b_mismatched_braces_end_to_end_via_target_graph_wrapping(self):
        # End-to-end: a malformed construct_query only surfaces the brace
        # mismatch when target_graph wrapping is requested (no wrapping
        # needed => no splitting attempted => no error from this code path).
        template = ConstructTemplate(
            name="malformed_template",
            description="test",
            construct_query="CONSTRUCT { ?s ?p ?o WHERE { ?s ?p ?o }",
        )
        with self.assertRaises(ValidationError):
            render_construct_template(
                template, params={}, target_graph="http://ex.org/graphs/g1"
            )

    def test_find_matching_brace_raises_on_no_closing_brace(self):
        with self.assertRaises(ValidationError):
            _find_matching_brace("{ unterminated", 0)


# ---------------------------------------------------------------------------
# execute_construct_template: render -> execute -> convert -> persist.
# Property 6 (round-trip persistence) coverage via a stub store_backend.
# ---------------------------------------------------------------------------


class _StubStoreBackend:
    """
    Minimal stub store_backend for execute_construct_template tests.

    execute_sparql returns a fixed CONSTRUCT-shaped result; add_triplets
    records exactly what it was called with and returns a caller-controlled
    success/failure result.
    """

    def __init__(self, triples, add_triplets_result=None, execute_sparql_error=None):
        # Normalize plain (s, p, o) 3-tuples to the real (s, p, o, metadata)
        # 4-tuple shape execute_sparql's CONSTRUCT path now returns, so
        # existing call sites that only care about subject/predicate/object
        # don't need to be rewritten; tests exercising metadata pass real
        # 4-tuples directly and are left untouched here.
        self._triples = [
            t if len(t) == 4 else (t[0], t[1], t[2], {}) for t in triples
        ]
        self._add_triplets_result = add_triplets_result or {"success": True}
        self._execute_sparql_error = execute_sparql_error
        self.execute_sparql_calls = []
        self.add_triplets_calls = []

    def execute_sparql(self, query, **options):
        self.execute_sparql_calls.append((query, options))
        if self._execute_sparql_error is not None:
            raise self._execute_sparql_error
        return {
            "success": True,
            "bindings": [],
            "variables": [],
            "triples": self._triples,
            "metadata": {"query": query, "result_format": "construct"},
        }

    def add_triplets(self, triplets, **options):
        self.add_triplets_calls.append((triplets, options))
        return self._add_triplets_result


class TestExecuteConstructTemplate(unittest.TestCase):
    def _template(self, target_graph=None):
        return ConstructTemplate(
            name="exec_test_template",
            description="test",
            construct_query=(
                "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
                "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
            ),
            parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
            target_graph=target_graph,
        )

    # --- 1. Happy path ---

    def test_happy_path_returns_triplets_and_calls_add_triplets_with_same_list(self):
        fixed_triples = [
            ("http://ex.org/s1", "http://ex.org/p1", "v1"),
            ("http://ex.org/s2", "http://ex.org/p2", "v2"),
        ]
        stub = _StubStoreBackend(triples=fixed_triples)
        template = self._template()

        result = execute_construct_template(template, {"value": "x"}, stub)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            [(t.subject, t.predicate, t.object) for t in result],
            fixed_triples,
        )
        for triplet in result:
            self.assertEqual(
                triplet.metadata, {"source": "construct_template", "template": "exec_test_template"}
            )
            # confidence is explicitly 1.0 (documented, deliberate default —
            # CONSTRUCT results are deterministic, not probabilistic extraction).
            self.assertEqual(triplet.confidence, 1.0)

    def test_datatype_and_language_metadata_round_trip_through_real_execute_sparql(self):
        # End-to-end round-trip: a real BlazegraphStore.execute_sparql CONSTRUCT
        # response (parsed via rdflib from a Turtle fixture containing an
        # xsd:integer-typed literal and an @en-language-tagged literal) must
        # carry that datatype/language info through execute_construct_template's
        # Triplet conversion — not just matching lexical string values.
        from unittest.mock import MagicMock, patch as mock_patch
        from semantica.triplet_store.blazegraph_store import BlazegraphStore

        turtle_fixture = (
            b"@prefix ex: <http://ex.org/> .\n"
            b'ex:s1 ex:p_age 42 .\n'
            b'ex:s2 ex:p_label "hello"@en .\n'
        )
        mock_response = MagicMock()
        mock_response.content = turtle_fixture
        mock_response.raise_for_status = MagicMock()

        with mock_patch.object(BlazegraphStore, "_connect", autospec=True):
            store = BlazegraphStore(endpoint="http://localhost:9999/blazegraph")
        store.connected = True

        with mock_patch(
            "semantica.triplet_store.blazegraph_store.requests.post",
            return_value=mock_response,
        ):
            real_query_result = store.execute_sparql(
                "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
            )

        # Sanity check on the raw execute_sparql output itself before going
        # through execute_construct_template, so a failure here pinpoints
        # whether the bug is in execute_sparql's metadata extraction or in
        # execute_construct_template's consumption of it.
        raw_by_subject = {s: (s, p, o, meta) for s, p, o, meta in real_query_result["triples"]}
        self.assertEqual(
            raw_by_subject["http://ex.org/s1"][3].get("datatype"),
            "http://www.w3.org/2001/XMLSchema#integer",
        )
        self.assertEqual(raw_by_subject["http://ex.org/s2"][3].get("language"), "en")

        class _RealResultStub:
            def execute_sparql(self, query, **options):
                return real_query_result

            def add_triplets(self, triplets, **options):
                self.persisted = triplets
                return {"success": True}

        stub = _RealResultStub()
        template = self._template()

        result = execute_construct_template(template, {"value": "x"}, stub)

        by_subject = {t.subject: t for t in result}

        age_triplet = by_subject["http://ex.org/s1"]
        self.assertEqual(age_triplet.object, "42")
        self.assertEqual(
            age_triplet.metadata.get("datatype"),
            "http://www.w3.org/2001/XMLSchema#integer",
        )
        self.assertNotIn("lang", age_triplet.metadata)

        label_triplet = by_subject["http://ex.org/s2"]
        self.assertEqual(label_triplet.object, "hello")
        self.assertEqual(label_triplet.metadata.get("lang"), "en")
        self.assertNotIn("datatype", label_triplet.metadata)

        # Confirm the round-tripped metadata is exactly what
        # BlazegraphStore._format_object_for_sparql reads when re-serializing
        # (metadata.get("datatype") / metadata.get("lang")), proving this is
        # not just a string match but a genuine type-preserving round trip
        # usable for real re-persistence.
        rendered_age = store._format_object_for_sparql(age_triplet)
        self.assertEqual(
            rendered_age,
            '"42"^^<http://www.w3.org/2001/XMLSchema#integer>',
        )
        rendered_label = store._format_object_for_sparql(label_triplet)
        self.assertEqual(rendered_label, '"hello"@en')

        # add_triplets was called with exactly the persisted triplets
        # (same objects that were returned), confirming the round-tripped
        # metadata made it all the way through the persistence step too.
        self.assertEqual(
            [(t.subject, t.predicate, t.object) for t in stub.persisted],
            [(t.subject, t.predicate, t.object) for t in result],
        )

    # --- 2. Missing add_triplets ---

    def test_missing_add_triplets_raises_before_execute_sparql_is_called(self):
        class NoAddTriplets:
            def execute_sparql(self, query, **options):
                raise AssertionError("execute_sparql must not be called")

        stub = NoAddTriplets()
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

    def test_missing_add_triplets_execute_sparql_not_called_verified_via_mock(self):
        stub = MagicMock()
        del stub.add_triplets  # hasattr(stub, "add_triplets") -> False
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

        stub.execute_sparql.assert_not_called()

    # --- 3. Missing execute_sparql ---

    def test_missing_execute_sparql_raises_before_any_query_attempted(self):
        stub = MagicMock()
        del stub.execute_sparql  # hasattr(stub, "execute_sparql") -> False
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

        stub.add_triplets.assert_not_called()

    # --- 4. add_triplets reports failure ---

    def test_add_triplets_failure_raises_processing_error_not_partial_list(self):
        fixed_triples = [("http://ex.org/s1", "http://ex.org/p1", "v1")]
        stub = _StubStoreBackend(
            triples=fixed_triples,
            add_triplets_result={"success": False, "error": "write failed"},
        )
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

        # add_triplets was still attempted (with the constructed triplets)...
        self.assertEqual(len(stub.add_triplets_calls), 1)
        # ...but the function must not have returned anything — verified by
        # the exception itself; there is no return value to inspect because
        # execute_construct_template raised instead of returning.

    # --- Fix 3: execute_sparql success=False must be checked before triples
    # conversion, not silently treated as an empty-triples success ---

    def test_execute_sparql_success_false_raises_processing_error(self):
        class _FailedExecuteSparqlBackend:
            def __init__(self):
                self.add_triplets_calls = []

            def execute_sparql(self, query, **options):
                return {
                    "success": False,
                    "error": "Blazegraph returned HTTP 500",
                    "bindings": [],
                    "variables": [],
                }

            def add_triplets(self, triplets, **options):
                self.add_triplets_calls.append((triplets, options))
                return {"success": True}

        stub = _FailedExecuteSparqlBackend()
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

        # The failure must be caught before ever reaching triples conversion
        # or persistence — add_triplets must never be called.
        self.assertEqual(stub.add_triplets_calls, [])

    def test_execute_sparql_success_false_with_no_triples_key_still_raises(self):
        # Even if a failed backend response happens to omit "triples"
        # entirely (rather than including an empty list), success=False
        # alone must be sufficient to raise — the bug being fixed is
        # "success is never checked", not "triples key is missing".
        class _FailedNoTriplesKeyBackend:
            def __init__(self):
                self.add_triplets_calls = []

            def execute_sparql(self, query, **options):
                return {"success": False}

            def add_triplets(self, triplets, **options):
                self.add_triplets_calls.append((triplets, options))
                return {"success": True}

        stub = _FailedNoTriplesKeyBackend()
        template = self._template()

        with self.assertRaises(ProcessingError):
            execute_construct_template(template, {"value": "x"}, stub)

        self.assertEqual(stub.add_triplets_calls, [])

    # --- 5. execute_sparql raises an exception ---

    def test_execute_sparql_exception_propagates_without_being_swallowed(self):
        # Report exactly what happens: execute_construct_template does not
        # wrap or catch exceptions raised by store_backend.execute_sparql —
        # it calls it directly and lets whatever it raises propagate as-is.
        network_error = ProcessingError("SPARQL query failed: connection refused")
        stub = _StubStoreBackend(triples=[], execute_sparql_error=network_error)
        template = self._template()

        with self.assertRaises(ProcessingError) as ctx:
            execute_construct_template(template, {"value": "x"}, stub)

        # The exact same exception instance propagates unchanged (not
        # re-wrapped in a new ProcessingError with a different message).
        self.assertIs(ctx.exception, network_error)

    def test_execute_sparql_arbitrary_exception_type_also_propagates_unwrapped(self):
        # Confirm this holds even for a non-ProcessingError exception type
        # (e.g. a raw network library error) — execute_construct_template
        # does not catch-and-wrap at all; add_triplets is simply never
        # reached because execute_sparql raised first.
        original_error = ConnectionError("simulated network failure")
        stub = _StubStoreBackend(triples=[], execute_sparql_error=original_error)
        template = self._template()

        with self.assertRaises(ConnectionError) as ctx:
            execute_construct_template(template, {"value": "x"}, stub)

        self.assertIs(ctx.exception, original_error)
        self.assertEqual(stub.add_triplets_calls, [])

    # --- 6. target_graph precedence ---

    def test_target_graph_argument_overrides_template_target_graph(self):
        stub = _StubStoreBackend(triples=[])
        template = self._template(target_graph="http://ex.org/graphs/template_default")

        execute_construct_template(
            template, {"value": "x"}, stub, target_graph="http://ex.org/graphs/explicit"
        )

        _, add_triplets_options = stub.add_triplets_calls[0]
        self.assertEqual(add_triplets_options["graph"], "http://ex.org/graphs/explicit")

    def test_template_target_graph_used_when_argument_is_none(self):
        stub = _StubStoreBackend(triples=[])
        template = self._template(target_graph="http://ex.org/graphs/template_default")

        execute_construct_template(template, {"value": "x"}, stub, target_graph=None)

        _, add_triplets_options = stub.add_triplets_calls[0]
        self.assertEqual(add_triplets_options["graph"], "http://ex.org/graphs/template_default")

    def test_no_target_graph_anywhere_passes_none_to_add_triplets(self):
        stub = _StubStoreBackend(triples=[])
        template = self._template(target_graph=None)

        execute_construct_template(template, {"value": "x"}, stub)

        _, add_triplets_options = stub.add_triplets_calls[0]
        self.assertIsNone(add_triplets_options["graph"])

    # --- Fix 4: caller-supplied "result_format"/"graph" in **options must
    # not crash with a duplicate-keyword TypeError, and must be overridden
    # by this function's own required values rather than silently honored ---

    def test_options_result_format_collision_does_not_raise_and_is_overridden(self):
        stub = _StubStoreBackend(triples=[("http://ex.org/s1", "http://ex.org/p1", "v1")])
        template = self._template()

        # Caller passes a conflicting result_format in **options; this must
        # not raise "got multiple values for keyword argument 'result_format'".
        execute_construct_template(
            template, {"value": "x"}, stub, result_format="bindings"
        )

        # The explicit "construct" value must have won — not the caller's
        # "bindings" override.
        _, execute_sparql_options = stub.execute_sparql_calls[0]
        self.assertEqual(execute_sparql_options["result_format"], "construct")

    def test_options_graph_collision_does_not_raise_and_is_overridden(self):
        stub = _StubStoreBackend(triples=[("http://ex.org/s1", "http://ex.org/p1", "v1")])
        template = self._template(target_graph="http://ex.org/graphs/correct")

        # Caller passes a conflicting graph in **options; this must not
        # raise "got multiple values for keyword argument 'graph'".
        execute_construct_template(
            template, {"value": "x"}, stub, graph="http://ex.org/graphs/wrong"
        )

        # The correctly-resolved effective_graph must have won — not the
        # caller's "wrong" override passed via **options.
        _, add_triplets_options = stub.add_triplets_calls[0]
        self.assertEqual(add_triplets_options["graph"], "http://ex.org/graphs/correct")

    def test_options_result_format_and_graph_collision_both_overridden_together(self):
        stub = _StubStoreBackend(triples=[("http://ex.org/s1", "http://ex.org/p1", "v1")])
        template = self._template()

        # Both keys colliding at once, combined with an explicit target_graph
        # argument (which itself must win over template.target_graph too).
        execute_construct_template(
            template,
            {"value": "x"},
            stub,
            target_graph="http://ex.org/graphs/explicit",
            result_format="bindings",
            graph="http://ex.org/graphs/wrong",
        )

        _, execute_sparql_options = stub.execute_sparql_calls[0]
        self.assertEqual(execute_sparql_options["result_format"], "construct")

        _, add_triplets_options = stub.add_triplets_calls[0]
        self.assertEqual(add_triplets_options["graph"], "http://ex.org/graphs/explicit")

    # --- 7. render_construct_template's ValidationError propagates unchanged ---

    def test_missing_required_param_validation_error_propagates_unwrapped(self):
        stub = _StubStoreBackend(triples=[])
        template = self._template()

        with self.assertRaises(ValidationError):
            # Omit the required "value" parameter entirely.
            execute_construct_template(template, {}, stub)

        # Rendering must have failed before any query was attempted.
        self.assertEqual(stub.execute_sparql_calls, [])
        self.assertEqual(stub.add_triplets_calls, [])


# ---------------------------------------------------------------------------
# construct_template_step_handler: pipeline integration.
# ---------------------------------------------------------------------------


class TestConstructTemplateStepHandler(unittest.TestCase):
    def _template(self):
        return ConstructTemplate(
            name="step_handler_template",
            description="test",
            construct_query=(
                "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
                "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
            ),
            parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
        )

    def test_missing_store_backend_raises_processing_error(self):
        registry = ConstructTemplateRegistry()
        registry.register(self._template())

        with self.assertRaises(ProcessingError):
            construct_template_step_handler(
                None,
                template_name="step_handler_template",
                params={"value": "x"},
                construct_template_registry=registry,
                # store_backend deliberately omitted
            )

    def test_missing_registry_raises_processing_error(self):
        stub = _StubStoreBackend(triples=[])

        with self.assertRaises(ProcessingError):
            construct_template_step_handler(
                None,
                template_name="step_handler_template",
                params={"value": "x"},
                store_backend=stub,
                # construct_template_registry deliberately omitted
            )

    def test_unknown_template_name_raises_validation_error(self):
        registry = ConstructTemplateRegistry()
        registry.register(self._template())
        stub = _StubStoreBackend(triples=[])

        with self.assertRaises(ValidationError):
            construct_template_step_handler(
                None,
                template_name="does_not_exist",
                params={"value": "x"},
                store_backend=stub,
                construct_template_registry=registry,
            )

    def test_happy_path_delegates_to_execute_construct_template(self):
        registry = ConstructTemplateRegistry()
        registry.register(self._template())
        fixed_triples = [("http://ex.org/s1", "http://ex.org/p1", "hello")]
        stub = _StubStoreBackend(triples=fixed_triples)

        result = construct_template_step_handler(
            None,
            template_name="step_handler_template",
            params={"value": "hello"},
            store_backend=stub,
            construct_template_registry=registry,
        )

        self.assertEqual(
            [(t.subject, t.predicate, t.object) for t in result],
            fixed_triples,
        )
        self.assertEqual(len(stub.add_triplets_calls), 1)

    def test_engine_config_fallback_used_when_options_omit_store_backend(self):
        registry = ConstructTemplateRegistry()
        registry.register(self._template())
        stub = _StubStoreBackend(triples=[("http://ex.org/s1", "http://ex.org/p1", "v")])

        result = construct_template_step_handler(
            None,
            template_name="step_handler_template",
            params={"value": "v"},
            engine_config={"store_backend": stub, "construct_template_registry": registry},
        )

        self.assertEqual(len(result), 1)


class TestConstructTemplatePipelineIntegration(unittest.TestCase):
    """
    End-to-end: PipelineBuilder.add_step with the "construct_template" step
    type -> ExecutionEngine.execute_pipeline with store_backend + registry
    passed in -> triplets returned/persisted. Confirms Requirement 7.5 (no
    PipelineStep/_execute_step changes needed) holds in practice.
    """

    def test_construct_template_step_executes_end_to_end_via_execution_engine(self):
        from semantica.pipeline import ExecutionEngine, PipelineBuilder

        registry = ConstructTemplateRegistry()
        registry.register(
            ConstructTemplate(
                name="e2e_template",
                description="test",
                construct_query=(
                    "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
                    "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
                ),
                parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
            )
        )
        fixed_triples = [("http://ex.org/s1", "http://ex.org/p1", "Alice")]
        stub = _StubStoreBackend(triples=fixed_triples)

        builder = PipelineBuilder()
        builder.add_step(
            "apply_e2e_template",
            "construct_template",
            handler=construct_template_step_handler,
            template_name="e2e_template",
            params={"value": "Alice"},
            target_graph="http://ex.org/pipeline_graph",
        )
        pipeline = builder.build(name="e2e_pipeline")

        engine = ExecutionEngine()
        result = engine.execute_pipeline(
            pipeline,
            data=None,
            store_backend=stub,
            construct_template_registry=registry,
        )

        self.assertTrue(result.success, msg=f"Pipeline failed: {result.errors}")
        returned_triplets = result.output
        self.assertEqual(
            [(t.subject, t.predicate, t.object) for t in returned_triplets],
            fixed_triples,
        )
        # Confirm the triples were actually persisted via add_triplets.
        self.assertEqual(len(stub.add_triplets_calls), 1)
        persisted_triplets, options = stub.add_triplets_calls[0]
        self.assertEqual(
            [(t.subject, t.predicate, t.object) for t in persisted_triplets],
            fixed_triples,
        )
        # Verify the pipeline step config's target_graph passed through successfully.
        self.assertEqual(options.get("graph"), "http://ex.org/pipeline_graph")

    def test_construct_template_step_uses_template_target_graph_default(self):
        from semantica.pipeline import ExecutionEngine, PipelineBuilder

        registry = ConstructTemplateRegistry()
        registry.register(
            ConstructTemplate(
                name="e2e_template_with_default_graph",
                description="test",
                construct_query=(
                    "CONSTRUCT { <http://ex.org/s1> <http://ex.org/p1> {{value}} } "
                    "WHERE { <http://ex.org/s1> <http://ex.org/p1> {{value}} }"
                ),
                parameters=[ParameterDescriptor(name="value", type="literal", required=True)],
                target_graph="http://ex.org/template_default_graph",
            )
        )
        stub = _StubStoreBackend(triples=[("http://ex.org/s1", "http://ex.org/p1", "Bob")])

        builder = PipelineBuilder()
        builder.add_step(
            "apply_e2e_template_default_graph",
            "construct_template",
            handler=construct_template_step_handler,
            template_name="e2e_template_with_default_graph",
            params={"value": "Bob"},
            # Notice target_graph is intentionally omitted here to test the fallback.
        )
        pipeline = builder.build(name="e2e_pipeline_default_graph")

        engine = ExecutionEngine()
        result = engine.execute_pipeline(
            pipeline,
            data=None,
            store_backend=stub,
            construct_template_registry=registry,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(stub.add_triplets_calls), 1)
        _, options = stub.add_triplets_calls[0]
        # Verify the template's default target_graph flowed through successfully via pipeline path.
        self.assertEqual(options.get("graph"), "http://ex.org/template_default_graph")


if __name__ == "__main__":
    unittest.main()
