"""Guard the installed ETABS contract and every executable guide example."""

import ast
import json
import re
from pathlib import Path

import pytest

from build_api_index import build_python_call as build_chm_call
from build_api_index_typelib import DEFAULT_DLL, build_python_call, extract_enums, extract_from_typelib, merge_with_chm

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "etabs_mcp"
ENTRIES = json.loads((DATA / "etabs_api_index.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("cls", "method", "expected"),
    [
        ("cFrameObj", "AddByPoint", ["Name"]),
        ("cFrameObj", "AddByCoord", ["Name"]),
        ("cAreaObj", "AddByCoord", ["X", "Y", "Z", "Name"]),
        ("cAreaObj", "AddByPoint", ["Point", "Name"]),
    ],
)
def test_creation_contract_preserves_all_inout_parameters(cls, method, expected):
    entry = next(e for e in ENTRIES if e["class"] == cls and e["method"] == method)
    assert [p["name"] for p in entry["params"] if p["direction"] == "inout"] == expected
    assert ", Name, PropName" in entry["python_call"]
    assert f"# returns [{', '.join(expected)}, ret_code]" in entry["python_call"]


def test_chm_merge_cannot_restore_broken_call_conventions(tmp_path):
    previous = tmp_path / "old.json"
    previous.write_text(
        json.dumps(
            [
                {
                    "class": "cFrameObj",
                    "method": "AddByPoint",
                    "description": "Human description",
                    "python_call": "wrong()",
                }
            ]
        )
    )
    entry = {"class": "cFrameObj", "method": "AddByPoint", "python_call": "correct()"}
    merged = merge_with_chm([entry], previous)
    assert merged[0]["python_call"] == "correct()"
    assert merged[0]["description"] == "Human description"


def test_chm_builder_never_drops_ref_name():
    params = [{"name": n, "cs_type": "String"} for n in ("Point1", "Point2", "Name", "PropName")]
    result = build_chm_call(
        "model.FrameObj",
        "AddByPoint",
        params,
        "int AddByPoint(string Point1, string Point2, ref string Name, string PropName)",
    )
    assert "Point1, Point2, Name, PropName" in result
    assert "[Name, ret_code]" in result


def test_property_contract_does_not_call_the_property():
    assert "model.FrameObj\n" in build_python_call("model", "FrameObj", [], property_get=True)


def test_scalar_values_are_not_misreported_as_success_codes():
    assert "ret_code" not in build_python_call("model.PointObj", "Count", [])
    assert "returns str" in build_python_call("model", "GetModelFilename", [], return_type="str")


def test_shell_and_wall_enums_match_etabs_23():
    metadata = json.loads((DATA / "etabs_api_metadata.json").read_text())
    assert metadata["etabs_version"] == "23.3.2"
    enums = metadata["enums"]
    assert enums["eShellType"]["eShellType_ShellThin"] == 1
    assert enums["eShellType"]["eShellType_ShellThick"] == 2
    assert enums["eShellType"]["eShellType_Membrane"] == 3
    assert enums["eWallPropType"]["eWallPropType_Specified"] == 1


@pytest.mark.skipif(not DEFAULT_DLL.exists(), reason="ETABS 23 type library is not installed")
def test_bundled_contracts_match_the_installed_type_library():
    current = extract_from_typelib(DEFAULT_DLL)
    by_method = {(e["class"], e["method"]): e for e in current}
    assert len(current) == len(ENTRIES)
    for entry in ENTRIES:
        actual = by_method[entry["class"], entry["method"]]
        assert entry["params"] == actual["params"]
        assert entry["python_call"] == actual["python_call"]
    metadata = json.loads((DATA / "etabs_api_metadata.json").read_text())
    assert metadata["enums"] == extract_enums(DEFAULT_DLL)


def test_guide_creation_examples_preserve_name_argument():
    checked = 0
    for path in (DATA / "etabs_skills").glob("*/SKILL.md"):
        for match in re.finditer(r"```python\s*\n(.*?)```", path.read_text(encoding="utf-8"), re.S):
            try:
                tree = ast.parse(match[1])
            except SyntaxError:
                continue  # prose templates containing ellipses or placeholders
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                owner = node.func.value
                if not isinstance(owner, ast.Attribute) or owner.attr not in ("FrameObj", "AreaObj"):
                    continue
                if node.func.attr not in ("AddByPoint", "AddByCoord"):
                    continue
                index = 2 if node.func.attr == "AddByPoint" else 6 if owner.attr == "FrameObj" else 4
                if any(k.arg == "PropName" for k in node.keywords):
                    continue
                if len(node.args) > index:
                    name = node.args[index]
                    assert (isinstance(name, ast.Constant) and name.value == "") or (
                        isinstance(name, ast.Name) and name.id == "Name"
                    ), str(path)
                    checked += 1
    assert checked >= 100
