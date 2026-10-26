from etabs_mcp import environment


def test_mcp_reduced_environment_gets_windows_paths_without_changing_user_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(environment.sys, "platform", "win32")
    monkeypatch.setattr(environment, "_program_directories", lambda _: {
        "PROGRAMFILES": "registered-64", "PROGRAMW6432": "registered-64", "PROGRAMFILES(X86)": "registered-32"})
    env = {"SYSTEMROOT": str(tmp_path / "Windows"), "TEMP": "user-temp", "PROGRAMFILES": "user-override"}
    added = environment.ensure_windows_environment(env)
    assert env["PROGRAMFILES"] == "user-override"
    assert env["PROGRAMW6432"] == "registered-64"
    assert env["PROGRAMFILES(X86)"] == "registered-32"
    assert env["COMSPEC"] == str(tmp_path / "Windows" / "System32" / "cmd.exe")
    assert env["TMP"] == "user-temp"
    assert "PROGRAMFILES" not in added
    assert environment.ensure_windows_environment(env) == {}


def test_other_platforms_do_not_receive_windows_environment_variables(monkeypatch):
    monkeypatch.setattr(environment.sys, "platform", "linux")
    env = {"PATH": "keep"}
    assert environment.ensure_windows_environment(env) == {}
    assert env == {"PATH": "keep"}
