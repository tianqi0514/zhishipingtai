"""
Tests for issue #3: construct_template_registry support in PipelineValidator.

Covers four new behaviours and one explicit regression guard:
  1. No registry provided → WARNING-level issue, not an error (result still valid).
  2. Registry provided, template_name absent → ERROR.
  3. Registry provided, required param absent from step.config["params"] → ERROR.
  4. Registry provided, everything valid → no errors/warnings for that step.
  5. Regression: pipelines containing only non-construct_template steps see
     identical validation output whether or not construct_template_registry is passed.
"""

import unittest
from unittest.mock import MagicMock, patch

from semantica.pipeline.pipeline_builder import Pipeline, PipelineStep
from semantica.pipeline.pipeline_validator import PipelineValidator
from semantica.triplet_store.construct_templates import (
    ConstructTemplate,
    ConstructTemplateRegistry,
    ParameterDescriptor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_QUERY = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"


def _make_registry(*templates):
    """Return a ConstructTemplateRegistry pre-loaded with the given templates."""
    reg = ConstructTemplateRegistry()
    for t in templates:
        reg.register(t)
    return reg


def _make_template(name, *param_descriptors):
    return ConstructTemplate(
        name=name,
        description="test template",
        construct_query=_MINIMAL_QUERY,
        parameters=list(param_descriptors),
    )


def _make_pipeline(*steps):
    """Wrap PipelineStep objects in a minimal Pipeline."""
    return Pipeline(name="test_pipeline", steps=list(steps))


def _make_step(name, step_type, config=None):
    return PipelineStep(name=name, step_type=step_type, config=config or {})


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestPipelineValidatorConstructRegistry(unittest.TestCase):

    def setUp(self):
        self.tracker_patcher = patch(
            "semantica.utils.progress_tracker.get_progress_tracker"
        )
        mock_get_tracker = self.tracker_patcher.start()
        mock_tracker = MagicMock()
        mock_get_tracker.return_value = mock_tracker

    def tearDown(self):
        self.tracker_patcher.stop()

    # ------------------------------------------------------------------
    # Test 1: registry not provided -> warning, not error
    # ------------------------------------------------------------------

    def test_no_registry_yields_warning_not_error(self):
        """
        When construct_template_registry is None (the default), a construct_template
        step should produce exactly one WARNING mentioning the inability to check
        template existence, and the result should still be valid (no errors).
        """
        step = _make_step(
            "build_foaf",
            "construct_template",
            config={"template_name": "person_to_foaf", "params": {}},
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(pipeline)  # no registry kwarg

        self.assertTrue(result.valid, msg=f"Expected valid; errors={result.errors}")
        self.assertEqual(result.errors, [], msg="Expected no errors")
        # At least one warning about the missing registry
        registry_warnings = [
            w for w in result.warnings if "construct_template_registry" in w
        ]
        self.assertGreater(
            len(registry_warnings),
            0,
            msg=f"Expected a warning about missing registry; warnings={result.warnings}",
        )

    # ------------------------------------------------------------------
    # Test 2: registry provided, template_name absent -> error
    # ------------------------------------------------------------------

    def test_missing_template_name_in_registry_yields_error(self):
        """
        When a registry is provided but step.config["template_name"] is not
        registered in it, validate_pipeline must produce an ERROR and result.valid
        must be False.
        """
        # Registry has "other_template", not "person_to_foaf"
        registry = _make_registry(
            _make_template("other_template")
        )
        step = _make_step(
            "build_foaf",
            "construct_template",
            config={"template_name": "person_to_foaf", "params": {}},
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(
            pipeline, construct_template_registry=registry
        )

        self.assertFalse(result.valid, msg="Expected invalid result")
        template_errors = [e for e in result.errors if "person_to_foaf" in e]
        self.assertGreater(
            len(template_errors),
            0,
            msg=f"Expected error mentioning 'person_to_foaf'; errors={result.errors}",
        )

    def test_none_template_name_in_config_yields_error(self):
        """
        Edge case: step.config has no 'template_name' key at all.
        Registry is provided; should still produce an error.
        """
        registry = _make_registry(_make_template("some_template"))
        step = _make_step(
            "bad_step",
            "construct_template",
            config={"params": {}},  # no template_name key
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(
            pipeline, construct_template_registry=registry
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("template_name" in e or "None" in e for e in result.errors),
                        msg=f"Expected error about missing/None template_name; errors={result.errors}")

    # ------------------------------------------------------------------
    # Test 3: registry provided, required param absent -> error
    # ------------------------------------------------------------------

    def test_missing_required_param_yields_error(self):
        """
        When the registry is provided, the template is found, but a required
        parameter is absent from step.config["params"], validate_pipeline must
        produce an ERROR listing the missing parameter.
        """
        template = _make_template(
            "person_to_foaf",
            ParameterDescriptor(name="subject", type="uri", required=True),
            ParameterDescriptor(name="name", type="literal", required=True),
            ParameterDescriptor(name="lang", type="literal", required=False, default="en"),
        )
        registry = _make_registry(template)
        # Provides "subject" but omits the required "name"
        step = _make_step(
            "build_foaf",
            "construct_template",
            config={
                "template_name": "person_to_foaf",
                "params": {"subject": "http://example.org/alice"},
            },
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(
            pipeline, construct_template_registry=registry
        )

        self.assertFalse(result.valid, msg="Expected invalid result")
        missing_errors = [e for e in result.errors if "name" in e]
        self.assertGreater(
            len(missing_errors),
            0,
            msg=f"Expected error about missing 'name' param; errors={result.errors}",
        )

    def test_required_param_with_default_still_flagged_when_absent(self):
        """
        Correctness of the corrected check: required=True AND d.name not in
        provided_params -> error, even when d.default is not None.
        This verifies the 'and d.default is None' condition was NOT included,
        matching render_construct_template's actual runtime behavior.
        """
        template = _make_template(
            "tricky_template",
            ParameterDescriptor(
                name="subject",
                type="literal",
                required=True,
                default="fallback_value",  # default exists but required=True
            ),
        )
        registry = _make_registry(template)
        step = _make_step(
            "tricky_step",
            "construct_template",
            config={
                "template_name": "tricky_template",
                "params": {},  # subject not supplied
            },
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(
            pipeline, construct_template_registry=registry
        )

        # Must be an error even though default="fallback_value" exists,
        # because render_construct_template raises on required=True + no caller value.
        self.assertFalse(result.valid, msg=(
            "Expected invalid: required=True param with a default should still be "
            "flagged if absent from step.config['params']"
        ))
        self.assertTrue(
            any("subject" in e for e in result.errors),
            msg=f"Expected error mentioning 'subject'; errors={result.errors}",
        )

    # ------------------------------------------------------------------
    # Test 4: all valid -> no errors, no construct-related warnings
    # ------------------------------------------------------------------

    def test_all_valid_yields_no_issues(self):
        """
        Registry provided, template found, all required params supplied ->
        no errors, no construct_template-related warnings.
        """
        template = _make_template(
            "person_to_foaf",
            ParameterDescriptor(name="subject", type="uri", required=True),
            ParameterDescriptor(name="name", type="literal", required=True),
            ParameterDescriptor(name="lang", type="literal", required=False, default="en"),
        )
        registry = _make_registry(template)
        step = _make_step(
            "build_foaf",
            "construct_template",
            config={
                "template_name": "person_to_foaf",
                "params": {
                    "subject": "http://example.org/alice",
                    "name": "Alice",
                    # "lang" intentionally omitted -- optional, should not trigger error
                },
            },
        )
        pipeline = _make_pipeline(step)
        validator = PipelineValidator()

        result = validator.validate_pipeline(
            pipeline, construct_template_registry=registry
        )

        self.assertEqual(result.errors, [], msg=f"Expected no errors; got {result.errors}")
        # No construct-specific warnings (handler/config warnings from generic check
        # are fine -- the step has no handler and that's expected in this test fixture)
        construct_warnings = [
            w for w in result.warnings if "construct_template_registry" in w
        ]
        self.assertEqual(
            construct_warnings, [],
            msg=f"Expected no registry-related warnings; got {result.warnings}",
        )

    # ------------------------------------------------------------------
    # Test 5: regression -- non-construct_template steps unaffected
    # ------------------------------------------------------------------

    def test_non_construct_steps_unchanged(self):
        """
        Regression guard: validate_pipeline's output must be identical for a
        pipeline containing only non-construct_template steps, whether or not
        construct_template_registry is passed.

        Both calls (with and without the registry kwarg) must produce the same
        valid/errors/warnings, confirming zero behavior change for existing
        callers of any other step type.
        """
        steps = [
            _make_step("ingest", "file_ingest", config={"path": "/data"}),
            _make_step("parse", "document_parse", config={"format": "pdf"}),
            _make_step("embed", "embedding", config={"model": "openai"}),
        ]
        pipeline = _make_pipeline(*steps)
        validator = PipelineValidator()

        # Call without registry (existing behavior)
        result_without = validator.validate_pipeline(pipeline)
        # Call with a registry (should not affect these steps at all)
        dummy_registry = ConstructTemplateRegistry()
        result_with = validator.validate_pipeline(
            pipeline, construct_template_registry=dummy_registry
        )

        self.assertEqual(
            result_without.valid,
            result_with.valid,
            msg="valid flag changed for non-construct_template pipeline",
        )
        self.assertEqual(
            result_without.errors,
            result_with.errors,
            msg="errors changed for non-construct_template pipeline",
        )
        self.assertEqual(
            result_without.warnings,
            result_with.warnings,
            msg="warnings changed for non-construct_template pipeline",
        )

    def test_mixed_pipeline_only_construct_step_gets_warning(self):
        """
        A pipeline with mixed step types: only the construct_template step should
        receive the 'no registry' warning; other steps should be unaffected.
        """
        steps = [
            _make_step("ingest", "file_ingest", config={"path": "/data"}),
            _make_step(
                "build_graph",
                "construct_template",
                config={"template_name": "some_template", "params": {}},
            ),
            _make_step("embed", "embedding", config={"model": "openai"}),
        ]
        pipeline = _make_pipeline(*steps)
        validator = PipelineValidator()

        result = validator.validate_pipeline(pipeline)  # no registry

        self.assertTrue(result.valid, msg=f"Expected valid; errors={result.errors}")
        # Exactly one construct-registry warning (for "build_graph")
        registry_warnings = [
            w for w in result.warnings if "construct_template_registry" in w
        ]
        self.assertEqual(
            len(registry_warnings),
            1,
            msg=f"Expected exactly 1 registry warning; warnings={result.warnings}",
        )
        # The warning should mention the step name
        self.assertIn("build_graph", registry_warnings[0])


if __name__ == "__main__":
    unittest.main()
