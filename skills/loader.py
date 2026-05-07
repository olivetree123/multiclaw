from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str


def load_skill_summaries(skills_dir: Path = SKILLS_DIR) -> list[Skill]:
    skills = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        content = skill_file.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(content)
        name = frontmatter.get("name") or skill_file.parent.name
        description = frontmatter.get("description") or ""
        skills.append(Skill(name=name, description=description, path=str(skill_file)))
    return skills


def format_skill_summaries(skills: list[Skill]) -> str:
    if not skills:
        return ""

    lines = [
        "Use the load_skill tool to read the full SKILL.md before applying a skill.",
    ]
    for skill in skills:
        description = f": {skill.description}" if skill.description else ""
        lines.append(f"- {skill.name}{description}")
    return "\n".join(lines)


def load_skill(name: str, skills_dir: Path = SKILLS_DIR) -> dict[str, str]:
    normalized_name = name.strip().lower()
    for skill in load_skill_summaries(skills_dir):
        if skill.name.lower() == normalized_name:
            content = Path(skill.path).read_text(encoding="utf-8")
            return {
                "name": skill.name,
                "description": skill.description,
                "path": skill.path,
                "content": content,
            }
    raise ValueError(f"Unknown skill: {name}")


def _parse_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    frontmatter = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter
