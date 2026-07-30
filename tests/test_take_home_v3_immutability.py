"""Regression guard for the immutable take-home-v3 policy bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
HASH_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "take-home-v3-asset-hashes.json"
)
RULEBOOK_REGISTRY = PROJECT_ROOT / "data" / "rulebooks" / "registry.json"


def test_take_home_v3_asset_set_and_bytes_are_immutable() -> None:
    fixture = json.loads(HASH_FIXTURE.read_text(encoding="utf-8"))
    bundle_root = PROJECT_ROOT / fixture["bundle_root"]
    expected = fixture["assets"]
    actual_paths = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.is_file()
    }

    assert actual_paths == set(expected)
    for relative_path, expected_hash in expected.items():
        content = (bundle_root / relative_path).read_bytes()
        assert sha256(content).hexdigest() == expected_hash, relative_path


def test_take_home_v3_is_additively_marked_non_production() -> None:
    registry = json.loads(RULEBOOK_REGISTRY.read_text(encoding="utf-8"))
    matches = [
        bundle
        for bundle in registry["bundles"]
        if bundle["rulebook_version"] == "take-home-v3"
    ]

    assert registry["registry_schema_version"] == (
        "rulebook-registry/1.0.0"
    )
    assert len(matches) == 1
    metadata = matches[0]
    assert metadata["lifecycle_status"] == (
        "SUPERSEDED_FOR_POLICY_FIDELITY"
    )
    assert metadata["superseded_by"] == "take-home-v4"
    assert metadata["historical_lineage_readable"] is True
    assert metadata["pipeline_validation_allowed"] is True
    assert metadata["production_policy_recommendation_allowed"] is False
