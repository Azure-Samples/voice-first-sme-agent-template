"""Tests for the rendering pipeline (scripts/render.py).

Treats the renderer as a pure function: JSON in -> system_prompt.txt content out.
Tests are hermetic via the render_fn fixture, which stages a temp agents/ dir.
"""

from __future__ import annotations

import json
import sys

import pytest


def strip_header(rendered: str) -> str:
    """Drop the 3 comment lines + 1 blank line that prefix every rendered file."""
    parts = rendered.split("\n", 4)
    assert len(parts) >= 5, "rendered output missing header"
    return parts[4]


SME_SECTION_HEADERS = [
    "# Who are you?",
    "# First principles",
    "# Goals",
    "# Domain configuration",
    "# Explanation model",
    "# Interaction patterns",
    "# Voice and delivery",
    "# Knowledge base and grounding",
    "# Response guidelines",
    "# Boundaries",
]

SALES_COACH_SECTION_HEADERS = [
    "# Who are you?",
    "# Coaching session structure",
    "# Sales framework",
    "# Consultative selling",
    "## Coaching prompts",
    "# Interaction loop",
    "# Voice and delivery",
    "# Coaching effectiveness",
    "# Knowledge base and grounding",
    "# Response guidelines",
    "# Mindset",
    "# Boundaries",
]

COACHING_PROMPT_TRIGGERS = [
    "Feature-focused",
    "Skipped stakeholders",
    "Unclear on value",
    "Stuck",
    "Stalled deal",
    "Weak differentiation",
]


def _assert_no_jinja_leakage(rendered: str) -> None:
    assert "{{" not in rendered, "unrendered Jinja expression in output"
    assert "{%" not in rendered, "unrendered Jinja statement in output"


def _assert_header_present(rendered: str) -> None:
    lines = rendered.splitlines()
    assert lines[0].startswith("# AUTO-GENERATED"), "missing AUTO-GENERATED line"
    assert lines[1].startswith("# Edit agents/"), "missing 'Edit agents/' line"
    assert lines[2].startswith("# Template:"), "missing 'Template:' line"
    assert lines[3] == "", "expected blank line after header"


def test_sme_starter_renders_correctly(render_fn, sme_starter):
    rendered = render_fn(sme_starter, name="azure_expert")

    _assert_header_present(rendered)
    body = strip_header(rendered)

    for section in SME_SECTION_HEADERS:
        assert section in body, f"missing section header: {section}"

    _assert_no_jinja_leakage(rendered)

    persona = sme_starter["persona"]
    assert persona["name"] in body
    assert persona["audience"] in body
    assert persona["tone"] in body

    assert 1000 < len(rendered) < 20000


def test_sales_coach_starter_renders_correctly(render_fn, sales_coach_starter):
    rendered = render_fn(sales_coach_starter, name="sales_coach")

    _assert_header_present(rendered)
    body = strip_header(rendered)

    for section in SALES_COACH_SECTION_HEADERS:
        assert section in body, f"missing section header: {section}"

    for trigger in COACHING_PROMPT_TRIGGERS:
        assert trigger in body, f"missing coaching prompt trigger: {trigger}"

    _assert_no_jinja_leakage(rendered)
    assert 1000 < len(rendered) < 20000


def test_unknown_use_case_fails_clearly(
    tmp_path, monkeypatch, capsys, render_module, sme_starter
):
    sme_starter["useCase"] = "nonexistent"

    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / "bad_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "customization.json").write_text(json.dumps(sme_starter))

    monkeypatch.setattr(render_module, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(render_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(render_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--agent", "bad_agent"])

    with pytest.raises(SystemExit) as exc_info:
        render_module.main()

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    # Either schema validation rejects the enum (preferred) or render_template
    # reports the unknown use case. Both paths must mention the bad value and
    # ideally the list of available use cases.
    err = captured.err
    assert "nonexistent" in err
    assert "sme" in err and "sales_coach" in err


def test_missing_required_field_fails_clearly(
    tmp_path, monkeypatch, capsys, render_module, sme_starter
):
    del sme_starter["persona"]["name"]

    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / "incomplete_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "customization.json").write_text(json.dumps(sme_starter))

    monkeypatch.setattr(render_module, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(render_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(render_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render.py", "--agent", "incomplete_agent"])

    with pytest.raises(SystemExit) as exc_info:
        render_module.main()

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "persona" in err
    assert "name" in err


def test_minimal_valid_json_renders(render_fn):
    minimal = {
        "schemaVersion": "1.0.0",
        "useCase": "sme",
        "persona": {"name": "Test Agent"},
    }
    rendered = render_fn(minimal, name="minimal_agent")

    _assert_header_present(rendered)
    body = strip_header(rendered)

    _assert_no_jinja_leakage(rendered)
    assert "Test Agent" in body
    assert len(rendered) > 200


# Anchor sentences sampled from the gold standards (after substituting starter
# values for placeholders). A failure here means the templates have likely
# dropped or significantly altered a section of the original prompt.
SME_GOLD_ANCHORS = [
    "You explain concepts, summarize source material, compare options, identify risks, answer domain questions",
    "A successful interaction helps the user leave with at least one of these outcomes:",
    "Use this model for most substantive answers. Move through it lightly and naturally.",
    "Use `search_knowledge_base` when the user asks about:",
    "Do not optimize for sounding comprehensive. Optimize for helping the user understand the right thing quickly and accurately.",
    "Never invent current product, compliance, licensing, pricing, roadmap, customer-specific, company-specific, policy, process, program, or implementation details.",
    "If the user writes or speaks in a language other than English, respond in that language.",
    "SPEAKING STYLE: Speak slowly and clearly.",
]

SALES_COACH_GOLD_ANCHORS = [
    "You are a coach, not a generic assistant. Do not just answer questions. Help the seller think, practice, self-critique, and improve.",
    "Reflect → Clarify → Elicit",
    "Help sellers find their own answers (Rogerian approach), but provide concrete guidance when needed.",
    "Use `search_knowledge_base` when the seller asks about:",
    "Insightful listening",
    "Situational fluency",
    "If the seller writes or speaks in a language other than English, respond in that language.",
    "SPEAKING STYLE: Speak slowly and clearly.",
]


def test_sme_rendered_preserves_gold_anchors(render_fn, sme_starter):
    rendered = render_fn(sme_starter, name="anchor_sme")
    missing = [a for a in SME_GOLD_ANCHORS if a not in rendered]
    assert not missing, (
        f"{len(missing)} SME gold-standard anchor(s) missing from rendered "
        f"output — a section may have been dropped:\n  - "
        + "\n  - ".join(missing)
    )


def test_sales_coach_rendered_preserves_gold_anchors(render_fn, sales_coach_starter):
    rendered = render_fn(sales_coach_starter, name="anchor_sc")
    missing = [a for a in SALES_COACH_GOLD_ANCHORS if a not in rendered]
    assert not missing, (
        f"{len(missing)} Sales Coach gold-standard anchor(s) missing from "
        f"rendered output — a section may have been dropped:\n  - "
        + "\n  - ".join(missing)
    )


def test_goals_render_in_both_templates(render_fn, sme_starter, sales_coach_starter):
    """Customer-supplied goals.outcome and goals.successDefinition must appear in
    the rendered prompt. Without this, the customization skill could collect goals
    that never reach the agent."""
    sme_rendered = render_fn(sme_starter, name="goals_sme")
    assert sme_starter["goals"]["outcome"] in sme_rendered
    assert sme_starter["goals"]["successDefinition"] in sme_rendered

    sc_rendered = render_fn(sales_coach_starter, name="goals_sc")
    assert sales_coach_starter["goals"]["outcome"] in sc_rendered
    assert sales_coach_starter["goals"]["successDefinition"] in sc_rendered


def test_persona_role_renders_in_both_templates(render_fn, sme_starter, sales_coach_starter):
    """persona.role must appear in the rendered prompt, not be silently shadowed
    by hardcoded template text. Uses a distinctive non-default value to confirm
    the template is actually substituting (not just matching the starter default)."""
    sme_starter["persona"]["role"] = "compliance advisor"
    sme_rendered = render_fn(sme_starter, name="role_sme")
    assert "compliance advisor" in sme_rendered
    assert "subject matter expert" not in sme_rendered

    sales_coach_starter["persona"]["role"] = "enablement coach"
    sc_rendered = render_fn(sales_coach_starter, name="role_sc")
    assert "enablement coach" in sc_rendered
    assert "sales coaching assistant" not in sc_rendered


def test_header_format(render_module):
    header = render_module.header_for("sme", "1.0.0", "azure_expert")
    lines = header.split("\n")

    assert lines[0] == "# AUTO-GENERATED — do not edit this file directly."
    assert lines[1] == "# Edit agents/azure_expert/customization.json instead, then run ./render."
    assert lines[2] == "# Template: sme.j2 | Schema: 1.0.0"
    assert lines[3] == ""
    assert lines[4] == ""


def test_header_is_deterministic(render_module):
    """Rendering twice with the same inputs produces byte-identical headers.

    Without this guarantee, committing system_prompt.txt as a diffable artifact
    is undermined — every render would dirty git even when nothing changed.
    """
    a = render_module.header_for("sme", "1.0.0", "azure_expert")
    b = render_module.header_for("sme", "1.0.0", "azure_expert")
    assert a == b


# ---------------------------------------------------------------------------
# sessionStructure (SKILL.md Item 4) and interactionLoop (Item 5)
#
# These tests cover the schema fields added to close the silent-input gap
# called out in PR 1 review. Each `type` branch of sessionStructure and the
# enabled/customPhrasing variants of interactionLoop must actually affect
# rendered output, in both templates where applicable.
# ---------------------------------------------------------------------------


def test_session_structure_staged_renders_custom_stages(render_fn, sales_coach_starter):
    sales_coach_starter["sessionStructure"] = {
        "type": "staged",
        "stages": [
            {"name": "Kickoff", "description": "Set the agenda and confirm the seller's goal for the session."},
            {"name": "Diagnosis", "description": "Identify the single deal-blocker driving the seller's question."},
            {"name": "Wrap", "description": "Commit to one action this week."},
        ],
    }
    rendered = render_fn(sales_coach_starter, name="ss_staged_sc")
    assert "**Kickoff**" in rendered
    assert "Set the agenda and confirm" in rendered
    assert "**Diagnosis**" in rendered
    assert "**Wrap**" in rendered
    # The default 4-stage block should be gone when custom stages are provided.
    assert "Ask 2-3 targeted questions about background" not in rendered


def test_session_structure_open_renders_followers_block(render_fn, sales_coach_starter):
    sales_coach_starter["sessionStructure"] = {"type": "open"}
    rendered = render_fn(sales_coach_starter, name="ss_open_sc")
    assert "Follow the seller's lead" in rendered
    assert "no fixed stage structure" in rendered
    # Default stages should not render in open mode.
    assert "**Orientation**" not in rendered


def test_session_structure_roleplay_renders_description_verbatim(render_fn, sales_coach_starter):
    custom_desc = "The agent plays a skeptical CFO. The seller pitches; the agent challenges value claims."
    sales_coach_starter["sessionStructure"] = {"type": "roleplay", "description": custom_desc}
    rendered = render_fn(sales_coach_starter, name="ss_roleplay_sc")
    assert custom_desc in rendered
    assert "**Orientation**" not in rendered


def test_session_structure_unset_falls_back_to_default_sc(render_fn, sales_coach_starter):
    """When sessionStructure is absent from a Sales Coach customization, the
    template falls back to the original 4-stage default — preserves backward
    compatibility for customizations that pre-date Item 4."""
    sales_coach_starter.pop("sessionStructure", None)
    rendered = render_fn(sales_coach_starter, name="ss_unset_sc")
    assert "**Orientation**" in rendered
    assert "**Understanding**" in rendered
    assert "**Intervention**" in rendered
    assert "**Next steps**" in rendered


def test_session_structure_staged_renders_in_sme(render_fn, sme_starter):
    """SME has no default Conversation structure section. When sessionStructure
    is set, the template inserts one before # Explanation model."""
    sme_starter["sessionStructure"] = {
        "type": "staged",
        "stages": [
            {"name": "Frame", "description": "Confirm the topic and the user's familiarity level."},
            {"name": "Explain", "description": "Walk through the concept layer by layer."},
        ],
    }
    rendered = render_fn(sme_starter, name="ss_staged_sme")
    assert "# Conversation structure" in rendered
    assert "**Frame**" in rendered
    assert "**Explain**" in rendered
    # Conversation structure should appear before the explanation model.
    assert rendered.index("# Conversation structure") < rendered.index("# Explanation model")


def test_session_structure_unset_omits_section_in_sme(render_fn, sme_starter):
    """Default SME (no sessionStructure) does not emit a Conversation
    structure section — preserves current SME rendering behavior."""
    sme_starter.pop("sessionStructure", None)
    rendered = render_fn(sme_starter, name="ss_unset_sme")
    assert "# Conversation structure" not in rendered
    assert "# Explanation model" in rendered


def test_interaction_loop_disabled_suppresses_sc_sections(render_fn, sales_coach_starter):
    sales_coach_starter["interactionLoop"] = {"enabled": False}
    rendered = render_fn(sales_coach_starter, name="il_off_sc")
    assert "# Interaction loop" not in rendered
    assert "# Coaching effectiveness" not in rendered
    # Other Sales Coach sections still render — the rest of the prompt is intact.
    assert "# Coaching session structure" in rendered
    assert "# Knowledge base and grounding" in rendered


def test_interaction_loop_default_renders_for_sc(render_fn, sales_coach_starter):
    """Backward compatibility: a Sales Coach customization without an explicit
    interactionLoop key still renders the loop (and coaching effectiveness)."""
    sales_coach_starter.pop("interactionLoop", None)
    rendered = render_fn(sales_coach_starter, name="il_default_sc")
    assert "# Interaction loop" in rendered
    assert "# Coaching effectiveness" in rendered


def test_interaction_loop_enabled_adds_sme_section(render_fn, sme_starter):
    """SME starter ships with interactionLoop.enabled=true; the rendered
    prompt includes an SME-flavored interaction loop after # Interaction patterns."""
    rendered = render_fn(sme_starter, name="il_on_sme")
    assert "# Interaction loop" in rendered
    assert "For substantive teaching turns" in rendered
    assert rendered.index("# Interaction patterns") < rendered.index("# Interaction loop")
    assert rendered.index("# Interaction loop") < rendered.index("# Voice and delivery")


def test_interaction_loop_disabled_omits_sme_section(render_fn, sme_starter):
    sme_starter["interactionLoop"] = {"enabled": False}
    rendered = render_fn(sme_starter, name="il_off_sme")
    assert "# Interaction loop" not in rendered
    # The SME prompt is still valid.
    assert "# Explanation model" in rendered
    assert "# Voice and delivery" in rendered


def test_interaction_loop_custom_phrasing_renders_in_both(render_fn, sme_starter, sales_coach_starter):
    phrasing = "Ask: 'walk me through how you would explain this to your team lead.'"
    sme_starter["interactionLoop"] = {"enabled": True, "customPhrasing": phrasing}
    sales_coach_starter["interactionLoop"] = {"enabled": True, "customPhrasing": phrasing}

    sme_rendered = render_fn(sme_starter, name="il_phrasing_sme")
    sc_rendered = render_fn(sales_coach_starter, name="il_phrasing_sc")

    assert phrasing in sme_rendered
    assert phrasing in sc_rendered


# ---------------------------------------------------------------------------
# Mandatory cross-check: every schema field the customize-agent SKILL.md
# writes must be referenced by at least one Jinja2 template, or the customer's
# input is silently dropped — exactly the implicit-contract failure mode this
# initiative was designed to fix. PR 1 review found three instances of this
# gap; this test converts it from a code-review judgment into a build failure.
#
# When a SKILL.md item gains a new field, add an entry here AND add a hook
# to at least one template. Tests fail until both are in place.
# ---------------------------------------------------------------------------

# (field_path, list_of_substrings_to_find).
# Multiple substrings are alternatives — the field passes if any one appears
# in either template. Aliases (e.g. `vp = voiceProfile`) and Jinja2 control
# constructs (e.g. `interactionLoop.enabled` inside an `{% if %}`) all count
# as references.
SKILL_FIELDS_TO_TEMPLATE_PATTERNS = [
    # Item 1 — Goals, audience, success
    ("persona.audience", ["persona.audience"]),
    ("persona.role", ["persona.role"]),
    ("goals.outcome", ["goals.outcome"]),
    ("goals.successDefinition", ["goals.successDefinition"]),
    # Item 2 — Personality and tone
    ("persona.tone", ["persona.tone"]),
    # Item 3 — Methodology or framework
    ("methodology.name", ["methodology.name"]),
    ("methodology.summary", ["methodology.summary"]),
    ("methodology.keyPrinciples", ["methodology.keyPrinciples"]),
    ("coachingPrompts", ["coachingPrompts"]),
    # Item 4 — Conversation structure
    ("sessionStructure.type", ["sessionStructure.type"]),
    ("sessionStructure.stages", ["sessionStructure.stages"]),
    ("sessionStructure.description", ["sessionStructure.description"]),
    # Item 5 — Interaction loop
    ("interactionLoop.enabled", ["interactionLoop.enabled", "ilEnabled"]),
    ("interactionLoop.customPhrasing", ["interactionLoop.customPhrasing"]),
    # Item 6 — Voice traits
    ("voiceProfile.acknowledgments", ["vp.acknowledgments", "voiceProfile.acknowledgments"]),
    ("voiceProfile.emphasisStyle", ["vp.emphasisStyle", "voiceProfile.emphasisStyle"]),
    ("voiceProfile.framingStyle", ["vp.framingStyle", "voiceProfile.framingStyle"]),
    ("voiceProfile.customTraits", ["vp.customTraits", "voiceProfile.customTraits"]),
    # Item 7 — Boundaries and refusals
    ("boundaries.customAdditions", ["boundaries.customAdditions"]),
    # Item 8 — Knowledge base usage rules
    ("knowledgeBase.triggerDescription", ["knowledgeBase.triggerDescription"]),
    ("knowledgeBase.incorporationStyle", ["knowledgeBase.incorporationStyle"]),
    ("knowledgeBase.noResultResponse", ["knowledgeBase.noResultResponse"]),
    # Item 0 — App title and greeting: frontendCopy.* is rendered by the
    # frontend (Docker build args), not the system prompt template. Not
    # asserted here. The settings.json double-write keeps the build args
    # wired until deploy.js reads frontendCopy directly.
]


def test_skill_writes_match_template_references(project_root):
    template_dir = project_root / "templates" / "system_prompt"
    templates = sorted(template_dir.glob("*.j2"))
    assert templates, f"no Jinja2 templates found in {template_dir}"
    combined = "\n".join(t.read_text(encoding="utf-8") for t in templates)

    missing = []
    for field_path, patterns in SKILL_FIELDS_TO_TEMPLATE_PATTERNS:
        if not any(p in combined for p in patterns):
            missing.append(f"{field_path} (looked for: {', '.join(patterns)})")

    assert not missing, (
        f"{len(missing)} SKILL.md field(s) not referenced by any template — "
        "silent-input gap. Either add a template hook or remove the field "
        "from the skill checklist:\n  - " + "\n  - ".join(missing)
    )
