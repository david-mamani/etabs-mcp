"""Protect existing work while preparing real MCP sessions without UI access."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client

from etabs_mcp import connection, server, session


def fake_model(path=None, count=0):
    state = {"path": path}
    def save(value):
        state["path"] = value
        return 0
    return SimpleNamespace(
        GetVersion=lambda: ("23.3.2", 0, 0), GetModelFilename=lambda _: state["path"],
        GetModelIsLocked=lambda: False,
        PointObj=SimpleNamespace(Count=lambda: count), FrameObj=SimpleNamespace(Count=lambda: 0),
        AreaObj=SimpleNamespace(Count=lambda: 0), InitializeNewModel=Mock(return_value=0),
        File=SimpleNamespace(NewGridOnly=Mock(return_value=0), Save=Mock(side_effect=save)),
        View=SimpleNamespace(RefreshView=Mock(return_value=0)),
    )


def setup(monkeypatch, tmp_path, active, models):
    registry = connection.InstanceRegistry()
    for item in active:
        registry.assign_alias(item.pid)
    registry.get_active_instances = lambda: active
    monkeypatch.setattr(session, "_find_etabs_pids", lambda: list(models))
    monkeypatch.setattr(session, "connect_and_run", lambda fn, pid, timeout: fn(models[pid]))
    monkeypatch.setattr(session, "_attach_etabs", lambda pid, timeout_s: SimpleNamespace(SapModel=models[pid]))
    monkeypatch.setattr(session, "_model_root", lambda: tmp_path / "models")
    return registry


def test_start_page_gets_saved_five_story_grid_without_any_desktop_api(monkeypatch, tmp_path):
    model = fake_model()
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, None, "23.3.2")], {10: model})
    result = session.prepare_etabs_session(registry, stories=5, grid_lines_x=7, spacing_x=5)
    assert result["success"] and result["model_ready"] and result["created_model"]
    assert result["computer_use_required"] is False
    assert result["pid"] == 10
    model.File.NewGridOnly.assert_called_once_with(5, 3, 3, 7, 2, 5, 6)
    assert result["saved_path"].endswith("model.EDB")
    assert result["model_path"] == result["saved_path"]


@pytest.mark.parametrize("path,count", [("user.edb", 0), (None, 5)])
def test_saved_empty_and_unsaved_populated_models_are_never_reset(monkeypatch, tmp_path, path, count):
    model = fake_model(path, count)
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, path, "23.3.2")], {10: model})
    result = session.prepare_etabs_session(registry)
    assert result["success"] and not result["created_model"]
    model.InitializeNewModel.assert_not_called()
    model.File.Save.assert_not_called()


def test_new_model_launches_separate_pid_and_leaves_user_model_untouched(monkeypatch, tmp_path):
    user, new = fake_model("user.edb", 3), fake_model()
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, "user.edb", "23.3.2")], {10: user, 20: new})
    monkeypatch.setattr(session, "find_etabs_exe", lambda: tmp_path / "ETABS.exe")
    launch = Mock(return_value=20)
    monkeypatch.setattr(session, "_launch_etabs", launch)
    result = session.prepare_etabs_session(registry, new_model=True)
    assert result["pid"] == 20 and result["alias"] == "etabs2" and result["launched"]
    user.InitializeNewModel.assert_not_called()
    user.File.Save.assert_not_called()
    launch.assert_called_once()


def test_closed_etabs_is_launched_and_prepared(monkeypatch, tmp_path):
    registry = setup(monkeypatch, tmp_path, [], {20: fake_model()})
    monkeypatch.setattr(session, "find_etabs_exe", lambda: tmp_path / "ETABS.exe")
    monkeypatch.setattr(session, "_launch_etabs", lambda *a, **k: 20)
    result = session.prepare_etabs_session(registry)
    assert result["success"] and result["launched"] and result["created_model"]


def test_multiple_instances_require_selection(monkeypatch, tmp_path):
    items = [connection.ETABSInstance(f"etabs{pid}", pid, None, "23.3.2") for pid in (1, 2)]
    registry = setup(monkeypatch, tmp_path, items, {1: fake_model(), 2: fake_model()})
    with pytest.raises(ValueError, match="Specify one"):
        session.prepare_etabs_session(registry)


def test_race_loading_a_file_before_reset_aborts_even_when_it_is_empty(tmp_path):
    model = fake_model("someone-just-opened.edb")
    with pytest.raises(session.ProtectedModelError):
        session._prepare_empty_model(model, tmp_path / "new.edb", (1, 3, 3, 2, 2, 6, 6), allow_existing=False)
    model.InitializeNewModel.assert_not_called()


def test_unique_work_files_do_not_reuse_a_shared_template(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "_model_root", lambda: tmp_path / "models")
    a, b = session._new_model_path(), session._new_model_path()
    assert a != b and a.parent.is_dir() and b.parent.is_dir()


def test_com_timeout_is_not_retried(monkeypatch, tmp_path):
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, None, "23.3.2")], {10: fake_model()})
    invoke = Mock(side_effect=TimeoutError("busy"))
    monkeypatch.setattr(session, "connect_and_run", invoke)
    with pytest.raises(TimeoutError, match="may still be running"):
        session.prepare_etabs_session(registry)
    assert invoke.call_count == 1


def test_invalid_pointer_on_start_page_retries_initialization_only_while_empty(monkeypatch, tmp_path):
    model = fake_model()
    model.File.NewGridOnly.side_effect = [OSError("Invalid pointer"), 0]
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, None, "23.3.2")], {10: model})
    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    result = session.prepare_etabs_session(registry)
    assert result["success"] and model.InitializeNewModel.call_count == 2
    model.File.Save.assert_called_once()


def test_save_failure_does_not_reset_the_initialized_model_again(monkeypatch, tmp_path):
    model = fake_model()
    model.File.Save.side_effect = None
    model.File.Save.return_value = 1
    registry = setup(monkeypatch, tmp_path, [connection.ETABSInstance("etabs1", 10, None, "23.3.2")], {10: model})
    with pytest.raises(session.ModelSaveError, match="Check write access"):
        session.prepare_etabs_session(registry)
    model.InitializeNewModel.assert_called_once()


async def test_prepare_session_is_exposed_to_mcp_clients(monkeypatch):
    prepare = Mock(return_value={"success": True, "pid": 123, "model_ready": True})
    monkeypatch.setattr(server, "prepare_etabs_session", prepare)
    async with Client(server.create_mcp_server([])) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert "prepare_session" in names
        response = await client.call_tool("prepare_session", {"new_model": True, "stories": 5})
        assert response.data["model_ready"]
        assert prepare.call_args.args[2:4] == (True, 5)
