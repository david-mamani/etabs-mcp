"""Companion installation is explicit, repeatable and preserves user edits."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from etabs_mcp import agent_skill, main, server


def test_install_is_idempotent_and_preserves_unrelated_files(tmp_path):
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep")
    first = agent_skill.install_agent_skill(tmp_path)
    second = agent_skill.install_agent_skill(tmp_path)
    assert first["changed"] and not second["changed"]
    assert Path(first["path"]).read_text(encoding="utf-8") == agent_skill.companion_skill_text()
    assert unrelated.read_text() == "keep"
    assert second["client_tools_disabled"] is False


def test_managed_upgrade_backs_up_exact_previous_content(monkeypatch, tmp_path):
    previous = b"old managed skill"
    (tmp_path / "SKILL.md").write_bytes(previous)
    marker = {"name": agent_skill.SKILL_NAME, "sha256": hashlib.sha256(previous).hexdigest()}
    (tmp_path / ".etabs-mcp.json").write_text(json.dumps(marker))
    report = agent_skill.install_agent_skill(tmp_path)
    assert Path(report["backup"]).read_bytes() == previous
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8") == agent_skill.companion_skill_text()


def test_user_edits_are_not_overwritten(tmp_path):
    agent_skill.install_agent_skill(tmp_path)
    target = tmp_path / "SKILL.md"
    target.write_text("my edited workflow")
    with pytest.raises(ValueError, match="local edits"):
        agent_skill.install_agent_skill(tmp_path)
    assert target.read_text() == "my edited workflow"


def test_unmanaged_skill_is_preserved(tmp_path):
    (tmp_path / "SKILL.md").write_text("another skill")
    with pytest.raises(ValueError, match="not managed"):
        agent_skill.install_agent_skill(tmp_path)
    assert (tmp_path / "SKILL.md").read_text() == "another skill"


def test_cli_installs_without_loading_server_or_connecting_etabs(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "etabs_mcp.server", None)
    monkeypatch.setitem(sys.modules, "etabs_mcp.connection", None)
    with pytest.raises(SystemExit) as stopped:
        main.main(["--install-skill", "--skill-dir", str(tmp_path)])
    assert stopped.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] and Path(result["path"]).is_file()


def test_destination_option_cannot_be_silently_ignored():
    with pytest.raises(SystemExit):
        main.parse_args(["--skill-dir", "something"])


async def test_mcp_initialize_and_discovery_share_the_policy():
    async with Client(server.create_mcp_server([])) as client:
        # Modern MCP negotiation uses discovery instead of InitializeResult.
        instructions = client.initialize_result.instructions if client.initialize_result else client.instructions
        assert instructions.startswith(agent_skill.API_ONLY_POLICY)
        discovery = await client.call_tool("discover_api", {})
        assert agent_skill.API_ONLY_POLICY in discovery.data
