"""Build comtypes call contracts from the installed ETABS type library.

The type library is authoritative for parameter direction, order, defaults,
return types and enum values. CHM data contributes descriptions only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

OUT_JSON = Path(__file__).parent / "src" / "etabs_mcp" / "etabs_api_index.json"
DEFAULT_DLL = Path(r"C:\Program Files\Computers and Structures\ETABS 23\NativeAPI\x64\ETABSv1.tlb")
VT_TO_PY = {
    0: "None",
    1: "None",
    2: "int",
    3: "int",
    4: "float",
    5: "float",
    8: "str",
    11: "bool",
    16: "int",
    17: "int",
    18: "int",
    19: "int",
    20: "int",
    21: "int",
    22: "int",
    23: "int",
    24: "None",
    25: "int",
}
COM_INTERNALS = {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo", "GetIDsOfNames", "Invoke"}


def class_name_to_model_path(class_name: str) -> str:
    if class_name == "cSapModel":
        return "model"
    return "model." + class_name.removeprefix("c")


def describe_type(info, desc) -> tuple[str, str]:
    vt = int(desc.vt)
    if vt == 26:  # VT_PTR, including pointers to SAFEARRAY and enum values
        return describe_type(info, desc._.lptdesc[0])
    if vt == 27:  # VT_SAFEARRAY
        cs_type, py_type = describe_type(info, desc._.lptdesc[0])
        return cs_type + "[]", f"list[{py_type}]"
    if vt == 29:  # VT_USERDEFINED
        ref = info.GetRefTypeInfo(desc._.hreftype)
        name = ref.GetDocumentation(-1)[0]
        return name, "int" if ref.GetTypeAttr().typekind == 0 else "Any"
    py_type = VT_TO_PY.get(vt, "Any")
    return py_type, py_type


def build_python_call(
    model_path: str, method: str, params: list[dict], *, property_get: bool = False, return_type: str = "int"
) -> str:
    # FIN and FOUT are independent. In/out parameters stay in the call AND
    # the returned tuple. In particular Name must precede PropName.
    inputs = [p for p in params if p["direction"] in ("in", "inout")]
    outputs = [p["name"] for p in params if p["direction"] in ("out", "inout")]
    args = ", ".join(p["name"] for p in inputs)
    call = f"{model_path}.{method}" + ("" if property_get else f"({args})")
    if property_get:
        returns = "# returns property value"
    elif outputs:
        returns = f"# returns [{', '.join(outputs)}, ret_code]"
    else:
        returns = f"# returns {return_type} (see method documentation for its meaning)"
    return f"result = {call}\n{returns}"


def extract_from_typelib(dll_path: Path) -> list[dict]:
    import comtypes.typeinfo

    lib = comtypes.typeinfo.LoadTypeLibEx(str(dll_path))
    entries = []
    links = []
    for i in range(lib.GetTypeInfoCount()):
        info = lib.GetTypeInfo(i)
        attr = info.GetTypeAttr()
        class_name = lib.GetDocumentation(i)[0]
        if attr.typekind not in (3, 4) or not class_name.startswith("c"):
            continue
        for j in range(attr.cFuncs):
            fd = info.GetFuncDesc(j)
            method, description, *_ = info.GetDocumentation(fd.memid)
            if fd.invkind in (4, 8) or method in COM_INTERNALS:
                continue
            names = info.GetNames(fd.memid, fd.cParams + 1)
            params = []
            for k in range(fd.cParams):
                elem = fd.lprgelemdescParam[k]
                paramdesc = elem._.paramdesc
                flags = int(paramdesc.wParamFlags)
                if flags & 8:  # FRETVAL is represented by the final return value
                    continue
                cs_type, py_type = describe_type(info, elem.tdesc)
                direction = "inout" if flags & 1 and flags & 2 else "out" if flags & 2 else "in"
                param = {
                    "name": names[k + 1],
                    "cs_type": cs_type,
                    "py_type": py_type,
                    "direction": direction,
                    "flags": flags,
                    "optional": bool(flags & 16),
                }
                if flags & 32:
                    param["default"] = paramdesc.pparamdescex[0].varDefaultValue.value
                params.append(param)
            ret_cs, ret_py = describe_type(info, fd.elemdescFunc.tdesc)
            property_get = fd.invkind == 2
            if property_get:
                links.append((class_name, method, ret_cs))
            entries.append(
                {
                    "title": f"{class_name}.{method} {'Property' if property_get else 'Method'}",
                    "qualified_name": f"ETABSv1.{class_name}.{method}",
                    "class": class_name,
                    "model_path": "",
                    "method": method,
                    "description": description or "",
                    "params": params,
                    "return_type": ret_py,
                    "property_get": property_get,
                    "cs_signature": f"{ret_cs} {method}({', '.join(('ref ' if p['direction'] == 'inout' else 'out ' if p['direction'] == 'out' else '') + p['cs_type'] + ' ' + p['name'] for p in params)})",
                    "source": "typelib",
                }
            )
    # Resolve real SapModel paths through interface-valued property getters.
    paths = {"cSapModel": "model"}
    for _ in range(len(links) + 1):
        changed = False
        for owner, prop, target in links:
            if owner in paths and target.startswith("c") and target not in paths:
                paths[target] = f"{paths[owner]}.{prop}"
                changed = True
        if not changed:
            break
    reachable = []
    for entry in entries:
        if entry["class"] not in paths:
            continue  # cHelper / cOAPI are not available through sandbox model
        entry["model_path"] = paths[entry["class"]]
        entry["python_call"] = build_python_call(
            entry["model_path"],
            entry["method"],
            entry["params"],
            property_get=entry["property_get"],
            return_type=entry["return_type"],
        )
        reachable.append(entry)
    return sorted(reachable, key=lambda e: (e["class"], e["method"]))


def extract_enums(dll_path: Path) -> dict[str, dict[str, int]]:
    import comtypes.typeinfo

    lib = comtypes.typeinfo.LoadTypeLibEx(str(dll_path))
    enums = {}
    for i in range(lib.GetTypeInfoCount()):
        info = lib.GetTypeInfo(i)
        attr = info.GetTypeAttr()
        if attr.typekind != 0:
            continue
        name = lib.GetDocumentation(i)[0]
        values = {}
        for j in range(attr.cVars):
            var = info.GetVarDesc(j)
            values[info.GetDocumentation(var.memid)[0]] = int(var._.lpvarValue[0].value)
        enums[name] = values
    return enums


def merge_with_chm(typelib_entries: list[dict], chm_json_path: Path) -> list[dict]:
    """Keep runtime contracts authoritative; merge only human descriptions."""
    if chm_json_path.exists():
        previous = json.loads(chm_json_path.read_text(encoding="utf-8"))
        descriptions = {(e["class"], e["method"]): e.get("description", "") for e in previous}
        for entry in typelib_entries:
            entry["description"] = descriptions.get((entry["class"], entry["method"]), "") or entry["description"]
    return typelib_entries


def update_verified_guides(entries: list[dict], metadata: dict, root: Path) -> None:
    """Regenerate the small authoritative contract references inside the guides."""
    import re

    contracts = {(e["class"], e["method"]): e for e in entries}
    for guide, classes in {
        "etabs-geometry": {"cFrameObj": ("AddByPoint", "AddByCoord"), "cAreaObj": ("AddByPoint", "AddByCoord")},
        "etabs-sections": {"cPropArea": ("SetSlab", "SetWall", "SetDeck", "GetSlab", "GetWall")},
    }.items():
        lines = [
            "<!-- BEGIN TYPELIB CONTRACT -->",
            "## Type-library validation",
            f"Validated against ETABS {metadata['etabs_version']}, `{metadata['type_library']}`.",
            f"Type-library SHA-256: `{metadata['sha256']}`.",
            "Parameters marked `inout` are inputs AND returned values. Preserve their order.",
            "For object creation, pass an empty `Name` before `PropName`. Check `result[-1] == 0`.",
        ]
        if guide == "etabs-sections":
            for enum in ("eShellType", "eWallPropType", "eSlabType", "eDeckType"):
                lines.extend(["", f"`{enum}`:"])
                lines.extend(f"- `{name} = {value}`" for name, value in metadata["enums"][enum].items())
        for cls, methods in classes.items():
            for method in methods:
                lines.extend(["", "```python", contracts[cls, method]["python_call"], "```"])
        lines.append("<!-- END TYPELIB CONTRACT -->")
        block = "\n".join(lines)
        path = root / guide / "SKILL.md"
        content = path.read_text(encoding="utf-8")
        if "<!-- BEGIN TYPELIB CONTRACT -->" in content:
            content = re.sub(
                r"<!-- BEGIN TYPELIB CONTRACT -->.*?<!-- END TYPELIB CONTRACT -->",
                lambda _, block=block: block,
                content,
                flags=re.S,
            )
        else:
            content = content.rstrip() + "\n\n" + block + "\n"
        path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", default=str(DEFAULT_DLL))
    parser.add_argument("--out", default=str(OUT_JSON))
    parser.add_argument("--typelib-only", action="store_true")
    parser.add_argument("--etabs-version", default="unknown", help="ETABS version used for validation")
    parser.add_argument("--update-guides", action="store_true")
    args = parser.parse_args()
    source, out = Path(args.dll), Path(args.out)
    entries = extract_from_typelib(source)
    if not args.typelib_only:
        entries = merge_with_chm(entries, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    metadata = {
        "etabs_version": args.etabs_version,
        "type_library": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "method_count": len(entries),
        "enums": extract_enums(source),
    }
    out.with_name("etabs_api_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if args.update_guides:
        update_verified_guides(entries, metadata, out.parent / "etabs_skills")
    print(f"Validated {len(entries)} methods and {len(metadata['enums'])} enums from {source.name}")


if __name__ == "__main__":
    main()
