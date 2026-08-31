"""
SPARQL CONSTRUCT Query Templates (Blazegraph-only)

This module provides parameterized SPARQL CONSTRUCT query templates: a
`ConstructTemplate` defines a reusable CONSTRUCT query body with `{{param}}`
placeholders, `ConstructTemplateRegistry` stores/retrieves templates by name,
and `render_construct_template` safely substitutes parameter values into a
validated, injection-safe SPARQL string.

This module is intentionally Blazegraph-only. `execute_construct_template`
renders a template, executes it via `store_backend.execute_sparql(...,
result_format="construct")` (the Blazegraph CONSTRUCT-aware extension),
converts the parsed RDF triples into `Triplet` objects, and persists them via
`store_backend.add_triplets`.

All literal escaping, URI allowlist validation, and datatype-IRI resolution
is delegated to the shared `semantica.triplet_store.sparql_escaping` module —
no escaping/validation logic is reimplemented here.

Main Classes:
    - ParameterDescriptor: Declares one template parameter's name/type/validation.
    - ConstructTemplate: Named, reusable CONSTRUCT query definition.
    - ConstructTemplateRegistry: Stores and retrieves ConstructTemplate instances.

Main Functions:
    - render_construct_template: Safely render a ConstructTemplate into SPARQL.
    - execute_construct_template: Render, execute, parse, and persist in one call.

Author: Semantica Contributors
License: MIT
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from ..semantic_extract.triplet_extractor import Triplet
from ..utils.exceptions import ProcessingError, ValidationError
from ..utils.logging import get_logger
from .sparql_escaping import escape_literal, resolve_datatype_iri, validate_uri

ParameterKind = Literal["uri", "literal", "typed-literal"]

# XSD local names (case-insensitive) that render as unquoted numeric/boolean
# literals rather than quoted "<value>"^^<iri> literals. Matched against the
# local name of the *resolved* datatype IRI so this works whether the
# descriptor's datatype was given as a prefixed name (xsd:integer), a full
# IRI, or a bracketed IRI.
_INTEGER_LOCAL_NAMES = frozenset({"integer", "int", "long", "short"})
_DECIMAL_LOCAL_NAMES = frozenset({"decimal", "double", "float"})
_BOOLEAN_LOCAL_NAMES = frozenset({"boolean"})
_NUMERIC_UNQUOTED_LOCAL_NAMES = _INTEGER_LOCAL_NAMES | _DECIMAL_LOCAL_NAMES | _BOOLEAN_LOCAL_NAMES

_CONSTRUCT_KEYWORD_RE = re.compile(r"\bCONSTRUCT\b", re.IGNORECASE)
_WHERE_KEYWORD_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{\{|\}\}")


@dataclass
class ParameterDescriptor:
    """Describes one substitution parameter accepted by a ConstructTemplate."""

    name: str
    """Placeholder name as it appears in the query body, e.g. "subject" for {{subject}}."""

    type: ParameterKind = "literal"
    """One of "uri" | "literal" | "typed-literal"."""

    required: bool = True
    """If True and no value/default is supplied at render time, render raises ValidationError."""

    default: Optional[Any] = None
    """Used when the caller omits this parameter and required=False."""

    datatype: Optional[str] = None
    """Only meaningful when type == "typed-literal". An XSD datatype token accepted by
    the shared resolve_datatype_iri, e.g. "xsd:integer", "xsd:dateTime", or a full IRI.
    Required when type == "typed-literal"; render_construct_template (and
    ConstructTemplateRegistry.register) raise ValidationError if type == "typed-literal"
    and datatype is None."""

    language: Optional[str] = None
    """Only meaningful when type == "literal". RFC 5646 language tag, e.g. "en"."""


@dataclass
class ConstructTemplate:
    """Parameterized SPARQL CONSTRUCT query template."""

    name: str
    """Unique registry key, e.g. "person_to_foaf"."""

    description: str
    """Human-readable summary, shown by list()/get_template_info()."""

    construct_query: str
    """Full CONSTRUCT query body containing {{param}} placeholders, e.g.:
    "CONSTRUCT { <{{subject}}> foaf:name {{name}} } WHERE { ... }"
    Must contain the CONSTRUCT keyword — enforced at register() time, not at
    dataclass construction time."""

    parameters: List[ParameterDescriptor] = field(default_factory=list)
    """Ordered list of accepted parameters. Order has no runtime meaning, only used for
    documentation / get_template_info() output."""

    target_graph: Optional[str] = None
    """Optional default named-graph IRI used when render_construct_template's/
    execute_construct_template's target_graph argument is not supplied. This value,
    like any caller-supplied target_graph, is ALWAYS passed through the same
    validate_uri/escape path as a "uri"-typed parameter before being interpolated —
    never through a raw f-string."""

    metadata: dict = field(default_factory=dict)
    """Free-form, mirrors PipelineTemplate.metadata (e.g. {"category": "rdf_mapping"})."""


class ConstructTemplateRegistry:
    """
    CONSTRUCT template management system (Blazegraph-only).

    Method shape mirrors PipelineTemplateManager:
        PipelineTemplateManager.register_template(template) -> None
        PipelineTemplateManager.get_template(name) -> Optional[PipelineTemplate]
        PipelineTemplateManager.list_templates(category=None) -> List[str]

    Unlike PipelineTemplateManager.register_template (which silently overwrites
    by name), this registry rejects duplicate names with ValidationError —
    CONSTRUCT queries execute against real triple stores, so silent overwrite
    is a correctness hazard.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Initialize template registry.

        Args:
            config: Configuration dictionary.
            **kwargs: Additional configuration options.
        """
        self.logger = get_logger("construct_template_registry")
        self.config = config or {}
        self.config.update(kwargs)
        self.templates: Dict[str, ConstructTemplate] = {}

    def register(self, template: ConstructTemplate) -> None:
        """
        Register a CONSTRUCT template.

        Args:
            template: ConstructTemplate to register.

        Raises:
            ValidationError: if template.name is already registered,
                construct_query does not contain the CONSTRUCT keyword, or any
                ParameterDescriptor with type == "typed-literal" is missing
                datatype. Validation happens before any mutation — a failed
                register() call never partially overwrites the registry.
        """
        if template.name in self.templates:
            raise ValidationError(
                f"Template already registered: {template.name!r}. "
                f"Remove it first via remove() if you intend to replace it."
            )

        if not _CONSTRUCT_KEYWORD_RE.search(template.construct_query):
            raise ValidationError(
                f"Template {template.name!r}: construct_query does not contain "
                f"the CONSTRUCT keyword."
            )

        for descriptor in template.parameters:
            if descriptor.type == "typed-literal" and descriptor.datatype is None:
                raise ValidationError(
                    f"Template {template.name!r}: parameter {descriptor.name!r} has "
                    f"type='typed-literal' but no datatype declared."
                )

        self.templates[template.name] = template
        self.logger.info(f"Registered CONSTRUCT template: {template.name}")

    def get(self, name: str) -> Optional[ConstructTemplate]:
        """
        Get template by name.

        Mirrors PipelineTemplateManager.get_template(template_name) -> Optional[PipelineTemplate].
        """
        return self.templates.get(name)

    def list(self, category: Optional[str] = None) -> List[str]:
        """
        List registered template names, optionally filtered by metadata["category"].

        Mirrors PipelineTemplateManager.list_templates(category=None) -> List[str].
        """
        if category:
            return [
                name
                for name, template in self.templates.items()
                if template.metadata.get("category") == category
            ]
        return list(self.templates.keys())

    def remove(self, name: str) -> bool:
        """
        Remove a template by name.

        Returns:
            True if a template was removed, False if name was not registered.
        """
        if name in self.templates:
            del self.templates[name]
            self.logger.info(f"Removed CONSTRUCT template: {name}")
            return True
        return False

    # --- PipelineTemplateManager-name aliases, for call-site consistency ---

    def register_template(self, template: ConstructTemplate) -> None:
        """Alias for register(), matching PipelineTemplateManager.register_template."""
        self.register(template)

    def get_template(self, template_name: str) -> Optional[ConstructTemplate]:
        """Alias for get(), matching PipelineTemplateManager.get_template."""
        return self.get(template_name)

    def list_templates(self, category: Optional[str] = None) -> List[str]:
        """Alias for list(), matching PipelineTemplateManager.list_templates."""
        return self.list(category)

    def get_template_info(self, template_name: str) -> Optional[Dict[str, Any]]:
        """
        Get template information.

        Mirrors PipelineTemplateManager.get_template_info.

        Returns:
            {"name", "description", "parameter_count", "target_graph", "metadata"}
            or None if template_name is not registered.
        """
        template = self.get(template_name)
        if not template:
            return None

        return {
            "name": template.name,
            "description": template.description,
            "parameter_count": len(template.parameters),
            "target_graph": template.target_graph,
            "metadata": template.metadata,
        }


# ---------------------------------------------------------------------------
# render_construct_template and its internal helpers
# ---------------------------------------------------------------------------


def _local_name_of_datatype_iri(datatype_iri: str) -> str:
    """Extract the lower-cased local name from a resolved (bracketed) datatype IRI."""
    inner = datatype_iri[1:-1] if datatype_iri.startswith("<") and datatype_iri.endswith(">") else datatype_iri
    if "#" in inner:
        return inner.rsplit("#", 1)[1].lower()
    if "/" in inner:
        return inner.rsplit("/", 1)[1].lower()
    return inner.lower()


def _render_numeric_literal(value: Any, local_name: str, descriptor_name: str) -> str:
    """
    Render an unquoted numeric/boolean literal for a "typed-literal" parameter
    whose resolved datatype local name is in _NUMERIC_UNQUOTED_LOCAL_NAMES.

    Raises:
        ValidationError: if value cannot be coerced to the declared datatype.
    """
    if local_name in _INTEGER_LOCAL_NAMES:
        if isinstance(value, bool):
            raise ValidationError(
                f"Parameter {descriptor_name!r}: expected an integer value for "
                f"datatype local name {local_name!r}, got boolean {value!r}."
            )
        try:
            return str(int(str(value)))
        except (TypeError, ValueError):
            raise ValidationError(
                f"Parameter {descriptor_name!r}: value {value!r} is not a valid "
                f"integer for its declared XSD datatype."
            )

    if local_name in _DECIMAL_LOCAL_NAMES:
        try:
            return str(float(str(value)))
        except (TypeError, ValueError):
            raise ValidationError(
                f"Parameter {descriptor_name!r}: value {value!r} is not a valid "
                f"decimal/double/float for its declared XSD datatype."
            )

    if local_name in _BOOLEAN_LOCAL_NAMES:
        if isinstance(value, bool):
            return "true" if value else "false"
        normalized = str(value).strip().lower()
        if normalized in ("true", "1"):
            return "true"
        if normalized in ("false", "0"):
            return "false"
        raise ValidationError(
            f"Parameter {descriptor_name!r}: value {value!r} is not a valid "
            f"boolean for its declared XSD datatype."
        )

    raise ValidationError(
        f"Parameter {descriptor_name!r}: datatype local name {local_name!r} is not "
        f"a recognized unquoted-numeric XSD datatype."
    )


def _find_matching_brace(text: str, open_index: int) -> int:
    """
    Given the index of an opening '{' in text, return the index of its matching '}'.

    Skips over double-quoted string literal content while scanning, so a '{'
    or '}' character that appears *inside* a rendered literal value (e.g. a
    "typed-literal"/"literal" parameter whose value contains a brace
    character — braces are not among the characters escape_literal escapes,
    since SPARQL/Turtle string literals don't require it) does not corrupt
    the brace-depth count. Quote-escaping is assumed to follow
    escape_literal's convention (\\\\ and \\" are the only two-character
    escapes that can produce a literal backslash or double-quote), so an
    unescaped '"' reliably toggles in/out of string-literal content.
    """
    depth = 0
    in_string = False
    i = open_index
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                # Skip the escaped character (e.g. \" or \\) without
                # inspecting it, matching escape_literal's escaping scheme.
                i += 2
                continue
            if ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValidationError("Unbalanced braces in construct_query: no matching '}' found.")


def _split_construct_query(query_body: str) -> Tuple[str, str, str]:
    """
    Split a fully-substituted CONSTRUCT query into (preamble, construct_clause,
    where_body), so the WHERE body can be re-wrapped in a GRAPH clause for
    target_graph support.

    Args:
        query_body: Fully-substituted query string (all {{name}} tokens
            already replaced).

    Returns:
        preamble: Everything before the CONSTRUCT keyword (e.g. PREFIX
            declarations), stripped of leading/trailing whitespace. Preserved
            verbatim so PREFIX declarations are not silently dropped when
            target_graph wrapping rewrites the CONSTRUCT/WHERE structure.
        construct_clause: The raw "{ ... }" template graph pattern immediately
            following CONSTRUCT, braces included.
        where_body: The raw text *inside* the "{ ... }" following WHERE,
            braces excluded.

    Raises:
        ValidationError: if the CONSTRUCT/WHERE structure cannot be located.
    """
    construct_match = _CONSTRUCT_KEYWORD_RE.search(query_body)
    if not construct_match:
        raise ValidationError("construct_query does not contain the CONSTRUCT keyword.")
    preamble = query_body[: construct_match.start()].strip()

    construct_open = query_body.find("{", construct_match.end())
    if construct_open == -1:
        raise ValidationError("construct_query is missing '{' after the CONSTRUCT keyword.")
    construct_close = _find_matching_brace(query_body, construct_open)
    construct_clause = query_body[construct_open : construct_close + 1]

    where_match = _WHERE_KEYWORD_RE.search(query_body, construct_close + 1)
    if not where_match:
        raise ValidationError("construct_query is missing a WHERE clause.")
    where_open = query_body.find("{", where_match.end())
    if where_open == -1:
        raise ValidationError("construct_query is missing '{' after the WHERE keyword.")
    where_close = _find_matching_brace(query_body, where_open)
    where_body = query_body[where_open + 1 : where_close]

    return preamble, construct_clause, where_body


def render_construct_template(
    template: ConstructTemplate,
    params: Dict[str, Any],
    target_graph: Optional[str] = None,
) -> str:
    """
    Render a ConstructTemplate's construct_query into a safe, executable SPARQL
    CONSTRUCT query string.

    Args:
        template: The ConstructTemplate to render.
        params: Values for each {{name}} placeholder, keyed by ParameterDescriptor.name.
            Values for missing optional parameters fall back to ParameterDescriptor.default.
        target_graph: Named graph IRI to wrap the CONSTRUCT query in (via a GRAPH
            clause around the WHERE body). If None, falls back to
            template.target_graph. If both are None, no graph wrapping is applied.

    Returns:
        Fully-substituted SPARQL CONSTRUCT query string, safe to pass directly to
        BlazegraphStore.execute_sparql.

    Raises:
        ValidationError: on any of:
            - a required ParameterDescriptor has no value in params and no default
            - a "uri"-typed parameter value fails validate_uri
            - a "typed-literal"-typed parameter is missing template-declared datatype
            - a "typed-literal"-typed parameter value cannot be coerced to that datatype
            - target_graph (explicit arg or template.target_graph) fails validate_uri
            - construct_query references a {{placeholder}} with no matching
              ParameterDescriptor (detected as an unresolved placeholder after
              substitution)
    """
    # Step 1: Resolve effective parameter values (params override, then default).
    resolved: Dict[str, Any] = {}
    for descriptor in template.parameters:
        if descriptor.name in params:
            resolved[descriptor.name] = params[descriptor.name]
        elif not descriptor.required:
            resolved[descriptor.name] = descriptor.default
        else:
            raise ValidationError(
                f"Missing required parameter: {descriptor.name!r} "
                f"(template {template.name!r})"
            )

    # Reject any keys in params that don't correspond to a declared
    # ParameterDescriptor — silently ignoring unknown parameters would let
    # a caller's typo (e.g. "subjcet" instead of "subject") go unnoticed
    # while the mistyped value is simply dropped.
    declared_names = {descriptor.name for descriptor in template.parameters}
    unexpected_keys = sorted(set(params) - declared_names)
    if unexpected_keys:
        raise ValidationError(
            f"Unexpected parameter(s) for template {template.name!r}: "
            f"{unexpected_keys}. Declared parameters: {sorted(declared_names)}."
        )

    # Step 2: Render each parameter value according to its declared type.
    rendered_values: Dict[str, str] = {}
    for descriptor in template.parameters:
        value = resolved[descriptor.name]

        if descriptor.type == "uri":
            safe_uri = validate_uri(value)
            rendered_values[descriptor.name] = f"<{safe_uri}>"

        elif descriptor.type == "literal":
            escaped = escape_literal(value)
            if descriptor.language is not None:
                rendered_values[descriptor.name] = f'"{escaped}"@{descriptor.language}'
            else:
                rendered_values[descriptor.name] = f'"{escaped}"'

        elif descriptor.type == "typed-literal":
            if descriptor.datatype is None:
                raise ValidationError(
                    f"typed-literal parameter requires datatype: {descriptor.name!r} "
                    f"(template {template.name!r})"
                )
            try:
                datatype_iri = resolve_datatype_iri(descriptor.datatype)
            except ValueError as exc:
                raise ValidationError(
                    f"Parameter {descriptor.name!r}: {exc}"
                ) from exc

            local_name = _local_name_of_datatype_iri(datatype_iri)
            if local_name in _NUMERIC_UNQUOTED_LOCAL_NAMES:
                rendered_values[descriptor.name] = _render_numeric_literal(
                    value, local_name, descriptor.name
                )
            else:
                escaped = escape_literal(value)
                rendered_values[descriptor.name] = f'"{escaped}"^^{datatype_iri}'

        else:
            raise ValidationError(
                f"Parameter {descriptor.name!r}: unknown parameter type {descriptor.type!r} "
                f"(template {template.name!r})"
            )

    # Step 3: Substitute {{name}} tokens in construct_query.
    query_body = template.construct_query
    for name, rendered in rendered_values.items():
        query_body = query_body.replace("{{" + name + "}}", rendered)

    if _PLACEHOLDER_RE.search(query_body):
        raise ValidationError(
            f"Unresolved placeholder(s) in construct_query for template "
            f"{template.name!r}: query still contains '{{{{' / '}}}}' tokens "
            f"with no matching ParameterDescriptor."
        )

    # Step 4: Resolve effective target_graph — SAME validate_uri path as any
    # "uri" parameter. No raw f-string interpolation is used here.
    effective_graph = target_graph if target_graph is not None else template.target_graph
    if effective_graph is not None:
        safe_graph_uri = validate_uri(effective_graph)
        wrapped_graph_token = f"<{safe_graph_uri}>"

        preamble, construct_clause, where_body = _split_construct_query(query_body)
        graph_wrapped_query = (
            f"CONSTRUCT {construct_clause} "
            f"WHERE {{ GRAPH {wrapped_graph_token} {{ {where_body} }} }}"
        )
        rendered_query = f"{preamble}\n{graph_wrapped_query}" if preamble else graph_wrapped_query
    else:
        rendered_query = query_body

    return rendered_query


def execute_construct_template(
    template: ConstructTemplate,
    params: Dict[str, Any],
    store_backend: Any,
    target_graph: Optional[str] = None,
    **options: Any,
) -> List[Triplet]:
    """
    Render, execute, parse, and persist a CONSTRUCT template in one call.

    Args:
        template: ConstructTemplate to execute.
        params: Parameter values, forwarded to render_construct_template.
        store_backend: A BlazegraphStore instance (Blazegraph-only; duck-typed
            via hasattr(store_backend, "execute_sparql"), but this function
            additionally requires store_backend to expose add_triplets — a
            plain SPARQL-only backend without write support is rejected with
            ProcessingError).
        target_graph: Forwarded to render_construct_template; also used as
            the `graph` option when persisting results via add_triplets so
            constructed triples land in the same named graph they were
            scoped to at query time.
        **options: Forwarded to both store_backend.execute_sparql and
            store_backend.add_triplets (e.g. timeout overrides). If options
            contains "result_format" and/or "graph", those keys are
            overridden by this function's own required values
            ("construct" and effective_graph respectively) rather than
            raising a duplicate-keyword-argument error — both keys are
            load-bearing internal details of what this function does, so a
            caller-supplied value for either is silently superseded, not an
            error condition.

    Returns:
        The List[Triplet] that were constructed AND successfully persisted
        via add_triplets. Order matches the order the store backend's
        execute_sparql yielded triples in its "triples" key.

    Raises:
        ValidationError: propagated from render_construct_template.
        ProcessingError: if store_backend lacks execute_sparql/add_triplets,
            or if persistence via add_triplets does not report success (either
            via a returned dict with success=False, or a raised ProcessingError
            from backends such as JenaStore that raise on a complete batch
            failure rather than returning a dict).

    Exception-propagation convention:
        This function does not wrap or catch exceptions raised by
        render_construct_template or store_backend.execute_sparql — each
        sub-layer is responsible for raising its own correctly-typed
        exception (ValidationError for rendering failures; ProcessingError
        for execution failures, as BlazegraphStore.execute_sparql already
        does internally for connection/request/Turtle-parse errors). Adding
        a second wrapping layer here would only obscure the original error
        with no new information. This extends to add_triplets: most backends
        signal failure via a returned dict (checked immediately after the call
        below), but some backends (e.g. JenaStore) raise ProcessingError
        directly on a complete batch failure — that exception is intentionally
        allowed to propagate uncaught here, as it is already a correctly-typed
        ProcessingError and carries the right diagnostic information.

    Why store_backend.execute_sparql is called directly instead of
    QueryEngine.execute_query (investigated for issue #322 item on reusing
    "Generic SPARQL execution ... QueryEngine.execute_query" — this is a
    deliberate choice, not an oversight):
        Routing through QueryEngine.execute_query was evaluated and found to
        introduce three concrete regressions against this function's already
        -tested behavior:
        1. QueryEngine.optimize_query's whitespace-collapse
           (" ".join(query.split())) corrupts literal content. Verified: a
           literal parameter value of "value:   three   spaces   " comes
           back from optimize_query as "value: three spaces " — the
           collapsing operates on the whole query string with no awareness
           of quoted-string boundaries, silently altering the literal's
           actual content. This breaks the escaping guarantees
           render_construct_template exists to provide (Property 1).
        2. QueryEngine.execute_query caches results keyed only on
           normalized query text (enable_caching=True by default). A
           CONSTRUCT query's correct results depend on the live state of the
           graph at query time; repeated execute_construct_template calls
           with identical params (a normal usage pattern — same template
           re-run periodically) would silently return a stale cached
           QueryResult instead of re-querying, causing incorrect
           persistence via add_triplets.
        3. QueryEngine.execute_query wraps its entire body in a blanket
           `except Exception: raise ProcessingError(...)`, re-typing every
           exception regardless of origin. This directly conflicts with the
           exception-propagation convention documented and tested above
           (e.g. a raw ConnectionError from store_backend.execute_sparql
           must propagate as ConnectionError, not get silently re-wrapped
           into a differently-worded ProcessingError).
        Fixing this properly would require QueryEngine itself to support a
        "do not touch this already-rendered, already-safe query" mode
        (disabling optimize_query and caching for CONSTRUCT) and to stop
        re-wrapping already-correctly-typed exceptions — changes to a
        shared, backend-agnostic module used by other query paths, which is
        out of scope for this Blazegraph-only feature per the issue's own
        no-scope-creep guidance. Calling store_backend.execute_sparql
        directly is therefore the correct choice today, not a gap to close
        casually.
    """
    if not (hasattr(store_backend, "execute_sparql") and hasattr(store_backend, "add_triplets")):
        raise ProcessingError(
            "store_backend must support both execute_sparql and add_triplets "
            "to use execute_construct_template."
        )

    # Step 1: Render (raises ValidationError unchanged on any failure).
    rendered_query = render_construct_template(template, params, target_graph)

    # Step 2: Execute via Blazegraph's CONSTRUCT-aware path. result_format is
    # a load-bearing internal detail of this function (CONSTRUCT parsing
    # requires it); if a caller's own **options happens to contain
    # "result_format", the explicit value here must win rather than raising
    # "got multiple values for keyword argument" — so it is popped out of a
    # local copy of options and re-applied explicitly.
    execute_options = dict(options)
    execute_options.pop("result_format", None)
    # Deliberately calling store_backend.execute_sparql directly, NOT
    # QueryEngine.execute_query — see "Why store_backend.execute_sparql is
    # called directly instead of QueryEngine.execute_query" in this
    # function's docstring before routing through QueryEngine here.
    query_result = store_backend.execute_sparql(
        rendered_query, result_format="construct", **execute_options
    )

    if not query_result.get("success", False):
        raise ProcessingError(
            f"CONSTRUCT query execution failed for template {template.name!r}: "
            f"{query_result}"
        )

    # Step 3: Convert parsed RDF triples to Triplet objects. store_backend is
    # a BlazegraphStore instance, whose execute_sparql returns Dict[str, Any]
    # with a "triples" key for CONSTRUCT queries: a list of
    # (subject, predicate, object, metadata) 4-tuples (see
    # BlazegraphStore.execute_sparql). object_metadata carries "datatype"
    # and/or "language" for literals that have that information — those are
    # folded into the resulting Triplet's own metadata under the
    # "datatype"/"lang" keys, which is exactly what
    # BlazegraphStore._format_object_for_sparql reads
    # (metadata.get("datatype") / metadata.get("lang")) when re-serializing
    # a Triplet back to SPARQL, so a typed/lang-tagged literal round-trips
    # correctly through add_triplets instead of being silently flattened to
    # an untyped plain string.
    raw_triples = query_result.get("triples", [])
    triplets: List[Triplet] = []
    for s, p, o, object_metadata in raw_triples:
        triplet_metadata = {"source": "construct_template", "template": template.name}
        if object_metadata.get("datatype"):
            triplet_metadata["datatype"] = object_metadata["datatype"]
        if object_metadata.get("language"):
            triplet_metadata["lang"] = object_metadata["language"]

        triplets.append(
            Triplet(
                subject=str(s),
                predicate=str(p),
                object=str(o),
                # confidence=1.0 (explicit, matching Triplet's own default):
                # a CONSTRUCT query is a deterministic graph transformation
                # over already-persisted RDF/SPARQL-computed data, not a
                # probabilistic extraction (unlike NER/LLM-based Triplet
                # extraction, where confidence reflects genuine estimation
                # uncertainty). There is no meaningful uncertainty to encode
                # here, so full confidence is the correct value, not an
                # accidental default.
                confidence=1.0,
                metadata=triplet_metadata,
            )
        )

    # Step 4: Persist via add_triplets (same write path as any other bulk
    # load). Same reasoning as the result_format pop above: "graph" is the
    # explicit target_graph/template.target_graph resolution this function
    # exists to enforce, so a caller-supplied "graph" in **options must not
    # crash the call or silently bypass that resolution — pop it before
    # forwarding and let the computed effective_graph win.
    effective_graph = target_graph if target_graph is not None else template.target_graph
    add_triplets_options = dict(options)
    add_triplets_options.pop("graph", None)
    write_result = store_backend.add_triplets(
        triplets, graph=effective_graph, **add_triplets_options
    )

    if not write_result.get("success", False):
        raise ProcessingError(
            f"Failed to persist constructed triples for template "
            f"{template.name!r}: {write_result}"
        )

    return triplets


def construct_template_step_handler(data: Any, **options: Any) -> List[Triplet]:
    """
    Pipeline step handler for the "construct_template" step type.

    Resolves `store_backend` and `construct_template_registry` from execution
    options, looks up the named template, and delegates to
    execute_construct_template. Mirrors the exact resolution pattern used for
    `triplet_store`/`version_manager` in
    ExecutionEngine._execute_step's delta_mode handling: call-time options
    first, engine config fallback second, ProcessingError if either is
    missing.

    Called by ExecutionEngine._execute_step as
    `step.handler(data, **step.config, **options)` — no change to
    PipelineStep or _execute_step is required, since step.config and
    **options already flow through generically for any step type.

    Args:
        data: Pipeline data flowing in (unused by this step type — a
            construct_template step's output is independent of upstream
            step data, exactly like other non-delta step types).
        **options: The merged step.config + execution-time options dict, as
            passed by ExecutionEngine._execute_step's
            `step.handler(data, **step.config, **options)` call. Expected
            keys:
                - template_name: str — required, key into
                  construct_template_registry.
                - params: Dict[str, Any] — required, forwarded to
                  render_construct_template.
                - target_graph: Optional[str] — optional.
                - store_backend: Any — resolved from options directly, or
                  from an "engine_config" dict passed alongside it (see
                  below); required.
                - construct_template_registry: ConstructTemplateRegistry —
                  resolved the same way; required.
                - engine_config: Optional[Dict[str, Any]] — fallback source
                  for store_backend/construct_template_registry when not
                  present directly in options, mirroring the *shape* of
                  self.config.get(...) in ExecutionEngine._execute_step's
                  delta_mode handling. One real difference from delta_mode:
                  delta_mode's fallback runs inside _execute_step itself (a
                  bound ExecutionEngine method with direct access to
                  self.config), whereas construct_template_step_handler is a
                  plain function with no such access — by design, this step
                  type requires no pre-handler interception in
                  _execute_step (see Requirement 7.5), so self.config is
                  simply never threaded to any step.handler call today. In
                  practice this means store_backend/construct_template_registry
                  should normally be passed as call-time options via
                  ExecutionEngine.execute_pipeline(pipeline, data,
                  store_backend=..., construct_template_registry=...),
                  which flow through untouched to this handler. The
                  engine_config parameter exists for callers who explicitly
                  forward their own ExecutionEngine(config=...) dict into
                  execute_pipeline's options (e.g. engine_config=engine.config)
                  and want the same two-tier resolution shape as delta_mode.
                - step_name: Optional[str] — used only to name the failing
                  step in error messages; defaults to "construct_template"
                  if not supplied (design.md's own pseudocode references
                  step.name inside this handler, but the actual
                  step.handler(data, **step.config, **options) invocation
                  never passes step.name to a standalone handler function —
                  this default covers that gap without requiring any
                  ExecutionEngine/_execute_step change).

    Returns:
        The List[Triplet] returned by execute_construct_template.

    Raises:
        ProcessingError: if store_backend or construct_template_registry
            cannot be resolved from options (or its "engine_config" fallback).
        ValidationError: if template_name is not registered in the resolved
            construct_template_registry.
    """
    step_name = options.get("step_name", "construct_template")
    engine_config = options.get("engine_config") or {}

    store_backend = options.get("store_backend") or engine_config.get("store_backend")
    construct_template_registry = options.get(
        "construct_template_registry"
    ) or engine_config.get("construct_template_registry")

    if not store_backend or not construct_template_registry:
        raise ProcessingError(
            f"Step '{step_name}' requires 'store_backend' and "
            f"'construct_template_registry' in execution options for "
            f"construct_template processing."
        )

    template_name = options.get("template_name")
    template = construct_template_registry.get(template_name)
    if template is None:
        raise ValidationError(f"Unknown construct template: {template_name!r}")

    return execute_construct_template(
        template=template,
        params=options.get("params", {}),
        store_backend=store_backend,
        target_graph=options.get("target_graph"),
    )
