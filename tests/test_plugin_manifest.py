"""The plugin launches the server from a git tag, so a release that bumps one version and not the
others ships manifests that start a different server than the one they describe."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "broker-analysis"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_both_manifests_carry_the_package_version():
    version = _package_version()
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["version"] == version
    assert _json(PLUGIN / ".codex-plugin" / "plugin.json")["version"] == version


def test_the_server_launches_from_the_tag_of_this_version():
    server = _json(PLUGIN / ".mcp.json")["mcpServers"]["broker-analysis"]
    source = server["args"][server["args"].index("--from") + 1]
    assert source == f"git+https://github.com/chetto1983/broker-analysis-mcp@v{_package_version()}"
    assert server["args"][-1] == "broker-analysis-mcp"


def test_every_skill_the_plugin_ships_names_its_own_directory():
    skills = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
    assert len(skills) == 5
    for name in skills:
        head = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        assert f"name: {name}\n" in head
