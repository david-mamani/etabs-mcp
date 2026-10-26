"""Exercise the actual MCP tools, including alias resolution and execution."""

from unittest.mock import Mock

from fastmcp import Client

from etabs_mcp import connection, server
from tests.test_status import model


async def test_status_and_code_select_the_same_pid(monkeypatch):
    registry = connection.InstanceRegistry()
    aliases = [registry.assign_alias(pid) for pid in (100, 200)]
    instances = [
        connection.ETABSInstance(alias, pid, path, "23.3.2")
        for alias, pid, path in zip(aliases, (100, 200), ("one.edb", "two.edb"), strict=True)
    ]
    registry.get_active_instances = lambda: instances
    monkeypatch.setattr(server, "InstanceRegistry", lambda: registry)
    invoke = Mock(side_effect=lambda fn, pid, timeout: fn(model({100: "one.edb", 200: "two.edb"}[pid], 12)))
    monkeypatch.setattr(server, "connect_and_run", invoke)
    async with Client(server.create_mcp_server([])) as client:
        ambiguous = await client.call_tool("get_status", {})
        assert not ambiguous.data["connected"]
        status = await client.call_tool("get_status", {"instance": "etabs2"})
        assert status.data["pid"] == 200
        assert status.data["model_path"] == "two.edb"
        code = await client.call_tool(
            "execute_code",
            {
                "instance": "etabs2",
                "code": "result = model.GetModelFilename(True)",
            },
        )
        assert code.data["success"]
        assert code.data["result"] == "two.edb"
        assert all(c.args[1] == 200 for c in invoke.call_args_list)


async def test_closed_alias_cannot_target_another_instance_with_the_same_pid(monkeypatch):
    registry = connection.InstanceRegistry()
    registry.assign_alias(100)
    registry._pid_to_alias.clear()  # process has closed; Windows later reuses its PID
    new_alias = registry.assign_alias(100)
    registry.get_active_instances = lambda: [connection.ETABSInstance(new_alias, 100, "new.edb", "23.3.2")]
    monkeypatch.setattr(server, "InstanceRegistry", lambda: registry)
    invoke = Mock()
    monkeypatch.setattr(server, "connect_and_run", invoke)
    async with Client(server.create_mcp_server([])) as client:
        result = await client.call_tool("get_status", {"instance": "etabs1"})
        assert not result.data["connected"]
        assert "no longer running" in result.data["error"]
    invoke.assert_not_called()


async def test_code_failure_identifies_alias_and_pid(monkeypatch):
    registry = connection.InstanceRegistry()
    registry.assign_alias(200)
    registry.get_active_instances = lambda: [connection.ETABSInstance("etabs1", 200, "two.edb", "23.3.2")]
    monkeypatch.setattr(server, "InstanceRegistry", lambda: registry)
    monkeypatch.setattr(server, "connect_and_run", Mock(side_effect=OSError("closed")))
    async with Client(server.create_mcp_server([])) as client:
        result = await client.call_tool("execute_code", {"instance": "etabs1", "code": "result = 1"})
        assert not result.data["success"]
        assert "etabs1 (PID 200)" in result.data["error"]
