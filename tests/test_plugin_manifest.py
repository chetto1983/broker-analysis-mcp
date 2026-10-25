"""The plugin launches the server from a git tag, so a release that bumps one version and not the
others ships manifests that start a different server than the one they describe."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "broker-analysis"

# Agent Plugins 1.0 (agentplugins/agent-plugins-spec, spec/1.0.0.md §5.2 and §7.2.1): the
# identifiers select the rules, and the manifest's top-level fields are a closed set.
AGENT_MANIFEST_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
AGENT_MANIFEST_FIELDS = {
    "$schema", "name", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "extensions",
}


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


def test_the_agent_plugins_manifest_carries_the_claude_metadata():
    agent = _json(PLUGIN / "plugin.json")
    assert agent["$schema"] == AGENT_MANIFEST_SCHEMA
    assert set(agent) <= AGENT_MANIFEST_FIELDS
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert {k: v for k, v in agent.items() if k != "$schema"} == claude


def test_the_agent_plugins_mcp_config_starts_the_same_server():
    agent = _json(PLUGIN / "mcp.json")
    assert set(agent) == {"$schema", "mcpServers"}
    assert agent["$schema"] == AGENT_MCP_SCHEMA
    assert agent["mcpServers"] == _json(PLUGIN / ".mcp.json")["mcpServers"]
    assert agent["mcpServers"]["broker-analysis"]["type"] == "stdio"


def test_every_skill_the_plugin_ships_names_its_own_directory():
    skills = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
    assert len(skills) == 5
    for name in skills:
        head = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        assert f"name: {name}\n" in head
