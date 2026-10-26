from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from etabs_mcp import selftest


def empty_model():
    return SimpleNamespace(
        GetModelFilename=lambda full: "",
        PointObj=SimpleNamespace(Count=lambda: 0),
        FrameObj=SimpleNamespace(Count=lambda: 0),
        AreaObj=SimpleNamespace(Count=lambda: 0),
    )


def test_signin_restart_reconnects_only_to_a_new_empty_process(monkeypatch):
    snapshots = iter([{100, 200}, {100, 300}])
    monkeypatch.setattr(selftest, "_etabs_pids", lambda: next(snapshots))
    monkeypatch.setattr(selftest.time, "sleep", lambda _: None)
    old, new = empty_model(), empty_model()
    attach = Mock(side_effect=lambda pid, timeout_s: SimpleNamespace(SapModel={200: old, 300: new}[pid]))
    monkeypatch.setattr(selftest, "_attach_etabs", attach)
    create = Mock(side_effect=[OSError("disposed object"), 0])
    monkeypatch.setattr(selftest, "_create_new_model", create)
    pid, _, selected = selftest._prepare_test_session(200, {100})
    assert pid == 300 and selected is new
    assert [c.args[0] for c in attach.call_args_list] == [200, 300]


def test_restart_never_initializes_an_existing_user_model(monkeypatch):
    monkeypatch.setattr(selftest, "_etabs_pids", lambda: {100, 300})
    target = empty_model()
    target.GetModelFilename = lambda full: "user-project.edb"
    monkeypatch.setattr(selftest, "_attach_etabs", lambda *a, **kw: SimpleNamespace(SapModel=target))
    create = Mock()
    monkeypatch.setattr(selftest, "_create_new_model", create)
    with pytest.raises(selftest.SelfTestError, match="no lo modificara"):
        selftest._prepare_test_session(200, {100})
    create.assert_not_called()


def test_ambiguous_restart_does_not_select_a_process(monkeypatch):
    monkeypatch.setattr(selftest, "_etabs_pids", lambda: {100, 300, 400})
    with pytest.raises(selftest.SelfTestError, match="ambiguo"):
        selftest._prepare_test_session(200, {100})


def test_smoke_rejects_default_steel_section():
    target = SimpleNamespace(
        FrameObj=SimpleNamespace(
            GetNameList=lambda: [16, [str(i) for i in range(16)], 0],
            GetSection=lambda name: ["W14X500", "", 0],
        )
    )
    with pytest.raises(selftest.SelfTestError, match="se esperaba SMOKE"):
        selftest._verify_frame_sections(target)


def test_smoke_passes_only_when_all_sixteen_sections_match():
    read = Mock(return_value=["SMOKE", "", 0])
    target = SimpleNamespace(
        FrameObj=SimpleNamespace(
            GetNameList=lambda: [16, [str(i) for i in range(16)], 0],
            GetSection=read,
        )
    )
    selftest._verify_frame_sections(target)
    assert read.call_count == 16


def test_smoke_assigns_smoke_as_property_instead_of_object_name():
    sections = {}

    def add_frame(p1, p2, Name="", PropName="Default"):
        name = str(len(sections) + 1)
        sections[name] = PropName
        return [name, 0]

    points = Mock(side_effect=lambda x, y, z: [f"{x},{y},{z}", 0])
    target = SimpleNamespace(
        SetPresentUnits=lambda units: 0,
        Story=SimpleNamespace(GetStories=lambda: [2, 0]),
        PointObj=SimpleNamespace(AddCartesian=points, SetRestraint=lambda *a: 0, SetLoadForce=lambda *a: 0),
        PropFrame=SimpleNamespace(SetRectangle=lambda *a: 0),
        FrameObj=SimpleNamespace(
            AddByPoint=add_frame,
            GetNameList=lambda: [len(sections), list(sections), 0],
            GetSection=lambda name: [sections[name], "", 0],
        ),
        LoadPatterns=SimpleNamespace(GetSelfWTMultiplier=lambda name: [1.0, 0]),
    )
    selftest._build_model(target)
    assert len(sections) == 16
    assert set(sections.values()) == {"SMOKE"}


def test_save_preserves_an_existing_smoke_file(monkeypatch, tmp_path):
    previous = tmp_path / "smoke.edb"
    previous.write_bytes(b"previous test")
    monkeypatch.setattr(selftest, "_output_path", lambda: previous)

    def save(path):
        from pathlib import Path

        Path(path).write_bytes(b"new model" * 1000)
        return 0

    out = selftest._save_model(SimpleNamespace(File=SimpleNamespace(Save=save)))
    assert out != previous
    assert previous.read_bytes() == b"previous test"
    assert out.exists()


def test_frozen_launcher_clears_and_restores_dll_search_path(monkeypatch, tmp_path):
    import os

    bundle = tmp_path / "bundle"
    system = tmp_path / "system"
    setter = Mock(return_value=1)
    monkeypatch.setattr(selftest.sys, "platform", "win32")
    monkeypatch.setattr(selftest.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(
        selftest.ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(SetDllDirectoryW=setter), raising=False
    )
    original_path = os.pathsep.join((str(bundle), str(bundle / "lib"), str(system)))
    monkeypatch.setenv("PATH", original_path)
    with pytest.raises(OSError, match="spawn failed"), selftest._external_program_environment() as env:
        assert env["PATH"] == str(system)
        assert os.environ["PATH"] == original_path
        raise OSError("spawn failed")
    assert [c.args[0] for c in setter.call_args_list] == [None, str(bundle)]
