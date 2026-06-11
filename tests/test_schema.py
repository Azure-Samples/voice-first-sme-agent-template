"""Tests for the customization JSON Schema (schemas/customization.schema.json)."""

from __future__ import annotations

from jsonschema import Draft202012Validator


def test_sme_starter_passes_schema_validation(schema, sme_starter):
    validator = Draft202012Validator(schema)
    assert validator.is_valid(sme_starter) is True


def test_sales_coach_starter_passes_schema_validation(schema, sales_coach_starter):
    validator = Draft202012Validator(schema)
    assert validator.is_valid(sales_coach_starter) is True


def test_unknown_fields_do_not_break_validation(schema, sme_starter):
    """Locks the additionalProperties: true policy for forward compatibility."""
    sme_starter["experimentalFeatureX"] = {"flag": True, "values": [1, 2, 3]}
    sme_starter["persona"]["experimental"] = "value"

    validator = Draft202012Validator(schema)
    assert validator.is_valid(sme_starter) is True


def test_missing_schema_version_fails_clearly(schema, sme_starter):
    del sme_starter["schemaVersion"]

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(sme_starter))

    assert len(errors) >= 1
    assert any("schemaVersion" in err.message for err in errors)


def test_missing_use_case_fails_clearly(schema, sme_starter):
    del sme_starter["useCase"]

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(sme_starter))

    assert len(errors) >= 1
    assert any("useCase" in err.message for err in errors)


def test_session_structure_rejects_invalid_type(schema, sme_starter):
    sme_starter["sessionStructure"] = {"type": "freeform"}

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(sme_starter))

    assert any("freeform" in err.message or "type" in err.absolute_path for err in errors)


def test_session_structure_stage_requires_name_and_description(schema, sme_starter):
    sme_starter["sessionStructure"] = {
        "type": "staged",
        "stages": [{"name": "Orient"}],
    }

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(sme_starter))

    assert any("description" in err.message for err in errors)


def test_staged_session_structure_requires_non_empty_stages(schema, sme_starter):
    validator = Draft202012Validator(schema)

    for session_structure in (
        {"type": "staged"},
        {"type": "staged", "stages": []},
    ):
        sme_starter["sessionStructure"] = session_structure
        errors = list(validator.iter_errors(sme_starter))

        assert errors, f"expected validation error for {session_structure}"
        assert any(
            "stages" in err.message
            or list(err.absolute_path) == ["sessionStructure", "stages"]
            for err in errors
        )


def test_roleplay_and_hybrid_session_structure_require_description(schema, sme_starter):
    validator = Draft202012Validator(schema)

    for session_type in ("roleplay", "hybrid"):
        for session_structure in (
            {"type": session_type},
            {"type": session_type, "description": ""},
        ):
            sme_starter["sessionStructure"] = session_structure
            errors = list(validator.iter_errors(sme_starter))

            assert errors, f"expected validation error for {session_structure}"
            assert any(
                "description" in err.message
                or list(err.absolute_path) == ["sessionStructure", "description"]
                for err in errors
            )
