"""Geometry snapshots and views must never need desktop APIs or model writes."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client
from PIL import Image

from etabs_mcp import connection, model_views, server


def example_model(units=6):
    coords = {
        "a": (0, 0, 0),
        "b": (4, 0, 0),
        "c": (4, 3, 0),
        "d": (0, 3, 0),
        "e": (0, 0, 3),
        "f": (4, 0, 3),
        "g": (4, 3, 3),
        "h": (0, 3, 3),
    }

    def names(values):
        return (len(values), tuple(values), 0)

    return SimpleNamespace(
        GetPresentUnits=lambda: units,
        GetModelFilename=lambda _: "test.edb",
        PointObj=SimpleNamespace(
            Count=lambda: 8, GetNameList=lambda: names(coords), GetCoordCartesian=lambda name: (*coords[name], 0)
        ),
        FrameObj=SimpleNamespace(Count=lambda: 1, GetNameList=lambda: names(["F1"]), GetPoints=lambda _: ("a", "e", 0)),
        AreaObj=SimpleNamespace(
            Count=lambda: 2,
            GetNameList=lambda: names(["A1", "W1"]),
            GetPoints=lambda name: (4, ("e", "f", "g", "h") if name == "A1" else ("a", "b", "f", "e"), 0),
            GetOpening=lambda _: (False, 0),
        ),
        Story=SimpleNamespace(
            GetStories=lambda: (1, ("Story1",), 0), GetElevation=lambda _: (3, 0), GetHeight=lambda _: (3, 0)
        ),
    )


@pytest.mark.parametrize("view", ["3d", "plan", "elevation"])
def test_all_views_are_png_from_geometry_only(view):
    data = model_views.read_model_geometry(example_model())
    png, meta = model_views.render_geometry(data, view, "Story1" if view == "plan" else None, 800, 600)
    image = Image.open(io.BytesIO(png))
    assert image.size == (800, 600) and image.format == "PNG"
    assert image.convert("L").getextrema()[0] < 100
    assert meta["desktop_access"] is False and meta["is_screenshot"] is False
    assert data["points"]["g"] == [4, 3, 3]


def test_etabs_units_are_converted_without_calling_a_setter():
    assert model_views.read_model_geometry(example_model(5))["points"]["g"] == [0.004, 0.003, 0.003]
    assert model_views.read_model_geometry(example_model(13))["points"]["g"] == [0.04, 0.03, 0.03]


def test_failed_com_read_is_not_silently_omitted():
    model = example_model()
    model.AreaObj.GetPoints = lambda name: (0, (), 1)
    with pytest.raises(RuntimeError, match="no incomplete drawing"):
        model_views.read_model_geometry(model)


def test_limits_and_unknown_story_fail_clearly():
    with pytest.raises(ValueError, match="limit"):
        model_views.read_model_geometry(example_model(), max_objects=2)
    data = model_views.read_model_geometry(example_model())
    with pytest.raises(ValueError, match="Unknown story"):
        model_views.render_geometry(data, "plan", "missing")


def test_openings_remain_openings():
    model = example_model()
    model.AreaObj.GetOpening = lambda name: (name == "A1", 0)
    data = model_views.read_model_geometry(model)
    assert data["areas"][0]["opening"] is True


def test_plan_opening_is_white_inside_the_floor():
    data = model_views.read_model_geometry(example_model())
    data["points"].update({"i": [1, 1, 3], "j": [3, 1, 3], "k": [3, 2, 3], "l": [1, 2, 3]})
    data["areas"].append({"name": "hole", "points": ["i", "j", "k", "l"], "opening": True})
    png, _ = model_views.render_geometry(data, "plan", "Story1", 800, 600)
    assert Image.open(io.BytesIO(png)).getpixel((400, 310)) == (255, 255, 255)


def test_model_change_during_read_is_rejected():
    model = example_model()
    model.FrameObj.Count = Mock(side_effect=[1, 2])
    with pytest.raises(ValueError, match="changed"):
        model_views.read_model_geometry(model)


def test_concave_areas_are_triangulated_without_filling_the_missing_corner():
    polygon = [(0, 0, 0), (3, 0, 0), (3, 1, 0), (1, 1, 0), (1, 3, 0), (0, 3, 0)]
    triangles = model_views._triangles(polygon)
    area = sum(abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2 for a, b, c in triangles)
    assert area == 5


async def test_mcp_returns_image_and_metadata_for_the_exact_instance(monkeypatch, tmp_path):
    registry = connection.InstanceRegistry()
    registry.assign_alias(321)
    registry.get_active_instances = lambda: [connection.ETABSInstance("etabs1", 321, "test.edb", "23.3.2")]
    monkeypatch.setattr(server, "InstanceRegistry", lambda: registry)
    invoke = Mock(side_effect=lambda fn, pid, timeout: fn(example_model()))
    monkeypatch.setattr(server, "connect_and_run", invoke)

    def save(png, view):
        path = tmp_path / f"{view}.png"
        path.write_bytes(png)
        return path

    monkeypatch.setattr(server, "save_render", save)
    async with Client(server.create_mcp_server([])) as client:
        response = await client.call_tool(
            "render_model_view", {"view": "plan", "story": "Story1", "instance": "etabs1"}
        )
        assert response.structured_content["pid"] == 321
        assert response.structured_content["desktop_access"] is False
        assert any(item.type == "image" for item in response.content)
        assert all(call.args[1] == 321 for call in invoke.call_args_list)
