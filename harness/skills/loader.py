"""P13 — Skills (on-demand SKILL.md loading).

Frontmatter is hand-parsed (no PyYAML — ground rule: stdlib first, add a
dependency only when stdlib genuinely can't do it; this format is simple
enough that it can). Skills are loaded into the Coder subagent's context
only when a kata's `category` matches one of a skill's `triggers` — never
unconditionally — and each load is a trace event so the UI can show
"skill loaded: algorithms" only on the runs where it actually happened.
"""
from __future__ import annotations

from pathlib import Path

from harness.models import Kata, Skill

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
_DELIM = "---"


def _parse_skill_md(path: Path) -> Skill:
    text = path.read_text()
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        raise ValueError(f"{path}: expected frontmatter starting with '---'")

    end = next(i for i in range(1, len(lines)) if lines[i].strip() == _DELIM)
    frontmatter = lines[1:end]
    body = "\n".join(lines[end + 1:]).strip()

    fields: dict[str, str] = {}
    for line in frontmatter:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    triggers = [t.strip() for t in fields.get("triggers", "").split(",") if t.strip()]
    return Skill(
        name=fields.get("name", path.parent.name),
        description=fields.get("description", ""),
        triggers=triggers,
        content=body,
    )


class SkillLoader:
    def __init__(self, skills_dir: Path | str = _SKILLS_DIR):
        self._skills_dir = Path(skills_dir)
        self._skills = self._load_all()

    def _load_all(self) -> list[Skill]:
        skills = []
        if not self._skills_dir.exists():
            return skills
        for path in sorted(self._skills_dir.glob("*/SKILL.md")):
            skills.append(_parse_skill_md(path))
        return skills

    def select(self, kata: Kata) -> list[Skill]:
        return [s for s in self._skills if kata.category in s.triggers]
