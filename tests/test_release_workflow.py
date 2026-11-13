"""The Release workflow takes its release notes from the CHANGELOG.md section for the tag (plan 007)."""
import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
CHANGELOG = "# Changelog\n\n## 0.2.1 - 2026-11-13\n\n- Newer.\n\n## 0.2.0 - 2026-11-12\n\n- First.\n- Second.\n"


def steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]


def notes_step(tmp_path, tag, changelog):
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    script = next(s["run"] for s in steps() if s.get("name") == "Release notes from CHANGELOG.md")
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=tmp_path,
                          env={"GITHUB_REF_NAME": tag, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
                          check=False)


def test_notes_hold_only_the_tagged_version_section(tmp_path):
    result = notes_step(tmp_path, "v0.2.0", CHANGELOG)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "notes.md").read_text().strip() == "- First.\n- Second."


def test_a_tag_without_a_changelog_section_fails(tmp_path):
    result = notes_step(tmp_path, "v0.3.0", CHANGELOG)
    assert result.returncode != 0
    assert "No CHANGELOG.md section with bullets for 0.3.0" in result.stderr


def test_the_release_uses_the_notes_file_not_generated_notes():
    create = next(s["run"] for s in steps() if "gh release create" in s.get("run", ""))
    assert "--notes-file notes.md" in create
    assert "--generate-notes" not in create
