"""P13 — SkillLoader."""
from harness.skills.loader import SkillLoader
from harness.verification.katas_loader import load


def test_loads_all_skills_from_repo_skills_dir():
    loader = SkillLoader()
    assert len(loader._skills) == 3
    names = {s.name for s in loader._skills}
    assert names == {"algorithms", "edge-cases", "python-idioms"}


def test_select_matches_kata_category_to_triggers():
    loader = SkillLoader()
    binary_search = load("binary_search")  # category: algorithms

    selected = loader.select(binary_search)

    assert any(s.name == "algorithms" for s in selected)
    assert all(binary_search.category in s.triggers for s in selected)


def test_select_returns_empty_for_unmatched_category():
    loader = SkillLoader()
    fake_kata = load("binary_search")
    fake_kata = fake_kata.__class__(**{**fake_kata.__dict__, "category": "no-such-category"})

    assert loader.select(fake_kata) == []
