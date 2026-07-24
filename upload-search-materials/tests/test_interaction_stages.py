from dataclasses import FrozenInstanceError

import pytest

from upload_search_materials.interaction.stages import STAGES, get_stage


def test_registry_contains_ordered_ten_stage_workflow():
    assert [stage.id for stage in STAGES] == [
        "setup", "completeness", "scope", "asset_matching", "image_review",
        "slots_copy", "dry_run", "approval", "production_confirmation", "results",
    ]


def test_every_interactive_stage_exposes_user_input_fields():
    for stage in STAGES:
        assert stage.fields


def test_video_field_is_present_and_deferred():
    field = next(f for f in get_stage("asset_matching").fields if f.name == "include_video")
    assert field.disabled is True
    assert field.help_text == "本轮测试延期"


def test_each_stage_declares_its_exact_fields_and_dependency():
    expected_fields = {
        "setup": (
            "store",
            "store_confirmed",
            "month",
            "product_scope",
            "product_ids",
            "products_csv",
            "rules_csv",
            "image_roots",
            "asset_manifest",
            "historical_basic_xlsx",
            "historical_promotion_csv",
            "user_notes",
        ),
        "completeness": ("confirmed_product_ids", "overrides", "user_notes"),
        "scope": ("decisions", "user_notes"),
        "asset_matching": ("image_roots", "source_types", "aliases", "folder_decisions", "license_decisions", "asset_decisions", "include_video", "user_notes"),
        "image_review": ("policy_path", "ratio_tolerance", "decisions", "user_notes"),
        "slots_copy": ("slot_assignments", "copy_edits", "user_notes"),
        "dry_run": ("decision", "warning_notes"),
        "approval": ("task_ids", "confirmed_by", "confirmed_at", "valid_until", "acknowledgement"),
        "production_confirmation": ("store", "product_ids", "task_ids", "slot_ids", "max_products", "approval_manifest_sha256", "final_confirmation", "notes"),
        "results": ("recovery_action", "manual_notes", "allow_retry_after_remote_absence"),
    }
    assert {stage.id: tuple(field.name for field in stage.fields) for stage in STAGES} == expected_fields
    assert [stage.previous_stage for stage in STAGES] == [None, *expected_fields.keys()][:-1]


def test_definitions_are_immutable_and_results_is_read_only():
    with pytest.raises(FrozenInstanceError):
        get_stage("setup").title = "changed"
    with pytest.raises(FrozenInstanceError):
        get_stage("setup").fields[0].label = "changed"
    assert get_stage("results").read_only is True


def test_stages_have_rendering_metadata_with_chinese_copy():
    for stage in STAGES:
        assert stage.title
        assert any("\u4e00" <= character <= "\u9fff" for character in stage.title)
        assert stage.description
        assert stage.component
        assert isinstance(stage.read_only, bool)


def test_setup_uses_configured_sources_and_keeps_manual_imports_optional():
    setup = get_stage("setup")
    setup_fields = {field.name: field for field in setup.fields}

    assert setup.exactly_one_constraints == ()
    assert setup_fields["products_csv"].component == "auto_path"
    assert setup_fields["rules_csv"].component == "auto_path"
    assert setup_fields["image_roots"].component == "auto_path_list"
    assert setup_fields["asset_manifest"].required is False
    assert setup_fields["historical_basic_xlsx"].required is False
    assert setup_fields["historical_promotion_csv"].required is False


def test_recovery_fields_are_conditionally_enabled_only_for_actionable_exceptions():
    results = get_stage("results")
    assert all(field.disabled for field in results.fields)
    assert {
        field.enabled_when for field in results.fields
    } == {"result.exception_requires_user_action"}
