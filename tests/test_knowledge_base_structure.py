from __future__ import annotations

from pathlib import Path


def test_knowledge_base_directory_exists(knowledge_base_root: Path) -> None:
    assert knowledge_base_root.exists()
    assert knowledge_base_root.is_dir()


def test_expected_ofa_domains_present(knowledge_base_root: Path) -> None:
    expected = {
        "FA - Fixed Assest",
        "GL- General Ledger",
        "OTL",
        "PA - Project Accounting",
    }
    present = {child.name for child in knowledge_base_root.iterdir() if child.is_dir()}
    assert expected.issubset(present)