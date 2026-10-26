from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from etabs_mcp import connection
from etabs_mcp.status import model_paths_match, read_model_status, run_status


def model(path="", count=0):
    return SimpleNamespace(
        GetVersion=lambda: ["23.3.2", 0, 0],
        GetModelFilename=lambda full: path,
        GetModelIsLocked=lambda: False,
        PointObj=SimpleNamespace(Count=lambda: count),
        FrameObj=SimpleNamespace(Count=lambda: 0),
        AreaObj=SimpleNamespace(Count=lambda: 0),
    )


@pytest.mark.parametrize(
    ("path", "count", "state", "ready"),
    [
        ("", 0, "empty", False),
        ("", 12, "unsaved", True),
        ("test.edb", 12, "loaded", True),
    ],
)
def test_connected_is_separate_from_model_ready(path, count, state, ready):
    result = read_model_status(model(path, count))
    assert result["connected"]
    assert result["model_state"] == state
    assert result["model_ready"] == ready


def test_unavailable_model_is_not_reported_as_empty():
    target = model()
    target.PointObj.Count = Mock(side_effect=OSError("disposed"))
    status = read_model_status(target)
    assert not status["model_ready"]
    assert status["model_state"] == "unavailable"
    assert status["model_empty"] is None


def test_working_filename_requires_exact_project_folder_and_stem(tmp_path):
    expected = tmp_path / "test.edb"
    assert model_paths_match(str(tmp_path / "test.$et"), expected)
    assert not model_paths_match(str(tmp_path / "other.$et"), expected)
    assert not model_paths_match(str(tmp_path / "other" / "test.$et"), expected)


def test_status_requires_the_test_pid_and_file(monkeypatch, tmp_path):
    path = str(tmp_path / "test.edb")
    instances = [
        connection.ETABSInstance("etabs1", 10, None, "23.3.2"),
        connection.ETABSInstance("etabs2", 20, path, "23.3.2"),
    ]
    monkeypatch.setattr(connection.InstanceRegistry, "get_active_instances", lambda self: instances)
    reader = Mock(side_effect=lambda fn, pid, timeout: fn(model(path, 12)))
    monkeypatch.setattr(connection, "connect_and_run", reader)
    assert run_status() == 1
    assert run_status(pid=10, expected_model=path) == 1
    assert run_status(pid=20, expected_model=path) == 0
    assert reader.call_args.args[1] == 20
