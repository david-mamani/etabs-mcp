"""Read ETABS geometry and render images without any desktop/window access.

The images are API-derived drawings, never screenshots of the ETABS application.
No mouse, keyboard, window handles, capture APIs or model setters are used here.
"""

from __future__ import annotations

import io
import math
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ViewKind = Literal["3d", "plan", "elevation"]
# Verified against ETABS 23.3.2 ETABSv1.tlb eUnits, not SAP2000's enum.
_LENGTH_SCALE = {
    1: 0.0254,
    2: 0.3048,
    3: 0.0254,
    4: 0.3048,
    5: 0.001,
    6: 1.0,
    7: 0.001,
    8: 1.0,
    9: 0.001,
    10: 1.0,
    11: 0.001,
    12: 1.0,
    13: 0.01,
    14: 0.01,
    15: 0.01,
    16: 0.01,
}


def _checked(value: Any, operation: str) -> Any:
    ret = value[-1] if isinstance(value, (tuple, list)) else value
    if ret != 0:
        raise RuntimeError(f"{operation} returned {ret!r}; no incomplete drawing was produced")
    return value


def read_model_geometry(model: Any, max_objects: int = 20000) -> dict:
    """Return a detached snapshot in metres, preserving units, lock and results."""
    if not 1 <= max_objects <= 100000:
        raise ValueError("max_objects must be between 1 and 100000")
    counts = [int(obj.Count()) for obj in (model.PointObj, model.FrameObj, model.AreaObj)]
    if sum(counts) > max_objects:
        raise ValueError(f"Model has {sum(counts)} objects; requested limit is {max_objects}")
    units = int(model.GetPresentUnits())
    if units not in _LENGTH_SCALE:
        raise ValueError(f"Unknown ETABS length units: {units}")
    scale = _LENGTH_SCALE[units]

    def names(obj: Any) -> list[str]:
        data = _checked(obj.GetNameList(), "GetNameList")
        return list(data[1]) if data[0] else []

    points = {}
    for name in names(model.PointObj):
        coord = _checked(model.PointObj.GetCoordCartesian(name), "PointObj.GetCoordCartesian")
        xyz = [float(value) * scale for value in coord[:3]]
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError(f"Non-finite coordinate in point {name}")
        points[name] = xyz
    frames = []
    for name in names(model.FrameObj):
        ends = _checked(model.FrameObj.GetPoints(name), "FrameObj.GetPoints")
        if any(point not in points for point in ends[:2]):
            raise ValueError("Model changed during export; retry on a stable model")
        frames.append({"name": name, "points": list(ends[:2])})
    areas = []
    for name in names(model.AreaObj):
        corners = _checked(model.AreaObj.GetPoints(name), "AreaObj.GetPoints")
        point_names = list(corners[1])
        if len(point_names) != corners[0] or any(point not in points for point in point_names):
            raise ValueError("Model changed during export; retry on a stable model")
        opening = _checked(model.AreaObj.GetOpening(name), "AreaObj.GetOpening")
        areas.append({"name": name, "points": point_names, "opening": bool(opening[0])})
    stories = []
    raw_stories = _checked(model.Story.GetStories(), "Story.GetStories")
    # GetStories includes Base in its returned arrays in ETABS 23.
    for name in raw_stories[1]:
        elevation = _checked(model.Story.GetElevation(name), "Story.GetElevation")[0]
        height = _checked(model.Story.GetHeight(name), "Story.GetHeight")[0]
        stories.append({"name": name, "elevation": float(elevation) * scale, "height": float(height) * scale})
    after = [int(obj.Count()) for obj in (model.PointObj, model.FrameObj, model.AreaObj)]
    if after != counts:
        raise ValueError("Model changed during export; retry on a stable model")
    return {
        "source": "ETABS COM API",
        "units": "m",
        "original_units": units,
        "model_path": model.GetModelFilename(True),
        "points": points,
        "frames": frames,
        "areas": areas,
        "stories": stories,
        "desktop_access": False,
        "is_screenshot": False,
    }


def _font(size: int, bold: bool = False) -> Any:
    try:
        return ImageFont.truetype(str(Path("C:/Windows/Fonts") / ("arialbd.ttf" if bold else "arial.ttf")), size)
    except OSError:
        return ImageFont.load_default(size=size)


def _plane(data: dict, story: str | None) -> tuple[float, float, str]:
    if not story:
        raise ValueError("Specify story for a plan view, for example Story3")
    matches = [item for item in data["stories"] if item["name"].casefold() == story.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Unknown story {story!r}. Available: {[s['name'] for s in data['stories']]}")
    item = matches[0]
    height = item["height"] if item["height"] > 0 else 3.0
    return item["elevation"], item["elevation"] - height / 2, item["name"]


def _triangles(vertices: list[tuple]) -> list[tuple]:
    """Triangulate a simple polygon, including concave area boundaries."""

    def cross(a: tuple, b: tuple, c: tuple) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True))
    if abs(signed) < 1e-7:
        return []  # area is viewed exactly edge-on
    orientation = 1 if signed > 0 else -1
    pending = list(vertices)
    triangles = []
    while len(pending) > 3:
        for i, b in enumerate(pending):
            a, c = pending[i - 1], pending[(i + 1) % len(pending)]
            if orientation * cross(a, b, c) <= 1e-7:
                continue
            others = [p for j, p in enumerate(pending) if j not in {(i - 1) % len(pending), i, (i + 1) % len(pending)}]
            if any(
                min(orientation * cross(a, b, p), orientation * cross(b, c, p), orientation * cross(c, a, p)) >= -1e-7
                for p in others
            ):
                continue
            triangles.append((a, b, c))
            pending.pop(i)
            break
        else:
            raise ValueError("Area cannot be triangulated; verify its boundary before rendering")
    if len(pending) == 3:
        triangles.append(tuple(pending))
    return triangles


def _solid_image(primitives: list, pixel: Any, view: str, size: tuple[int, int], ss: int) -> Image.Image:
    """Depth-test faces and edges; centroid sorting alone exposes hidden walls."""
    width, height = size
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    depth_buffer = np.full((height, width), -np.inf, dtype=np.float32)
    edges = []

    def vertex(p: list) -> tuple:
        x, y = pixel(p)
        depth = p[2] - p[0] - p[1] if view == "3d" else -p[1]
        return x, y, depth

    for _, kind, xyz in primitives:
        vertices = [vertex(p) for p in xyz]
        if kind in {"floor", "wall"}:
            color = 237 if kind == "floor" else 191
            for a, b, c in _triangles(vertices):
                denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
                if abs(denominator) < 1e-9:
                    continue
                x0 = max(0, math.floor(min(a[0], b[0], c[0])))
                x1 = min(width, math.ceil(max(a[0], b[0], c[0])) + 1)
                y0 = max(0, math.floor(min(a[1], b[1], c[1])))
                y1 = min(height, math.ceil(max(a[1], b[1], c[1])) + 1)
                if x1 <= x0 or y1 <= y0:
                    continue
                yy, xx = np.ogrid[y0:y1, x0:x1]
                u = ((b[1] - c[1]) * (xx + 0.5 - c[0]) + (c[0] - b[0]) * (yy + 0.5 - c[1])) / denominator
                v = ((c[1] - a[1]) * (xx + 0.5 - c[0]) + (a[0] - c[0]) * (yy + 0.5 - c[1])) / denominator
                w = 1 - u - v
                z = u * a[2] + v * b[2] + w * c[2]
                region = depth_buffer[y0:y1, x0:x1]
                visible = (u >= -1e-7) & (v >= -1e-7) & (w >= -1e-7) & (z > region)
                region[visible] = z[visible]
                canvas[y0:y1, x0:x1][visible] = color
            edges.extend((a, b, 140, ss) for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True))
        elif kind == "frame":
            edges.append((vertices[0], vertices[1], 45, 2 * ss))
    for a, b, color, stroke in edges:
        steps = max(2, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2) + 1)
        t = np.linspace(0, 1, steps)
        xs = np.rint(a[0] + t * (b[0] - a[0])).astype(int)
        ys = np.rint(a[1] + t * (b[1] - a[1])).astype(int)
        zs = a[2] + t * (b[2] - a[2])
        for dx in range(-(stroke // 2), stroke // 2 + 1):
            for dy in range(-(stroke // 2), stroke // 2 + 1):
                x, y = xs + dx, ys + dy
                valid = (x >= 0) & (x < width) & (y >= 0) & (y < height)
                x, y, z = x[valid], y[valid], zs[valid]
                visible = z >= depth_buffer[y, x] - 0.03
                canvas[y[visible], x[visible]] = color
    return Image.fromarray(canvas)


def render_geometry(
    data: dict, view: ViewKind = "3d", story: str | None = None, width: int = 1600, height: int = 1000
) -> tuple[bytes, dict]:
    """Orthographic geometry drawing. It does not open or alter ETABS views."""
    if view not in {"3d", "plan", "elevation"}:
        raise ValueError("view must be 3d, plan or elevation")
    if not 512 <= width <= 2400 or not 512 <= height <= 1800:
        raise ValueError("Image dimensions must be 512..2400 by 512..1800")
    if not data["frames"] and not data["areas"]:
        raise ValueError("The model has no frame or area geometry to draw")
    elevation, cut, story_name = _plane(data, story) if view == "plan" else (0.0, 0.0, "")
    points = data["points"]
    primitives = []
    tolerance = 1e-5
    for area in data["areas"]:
        xyz = [points[p] for p in area["points"]]
        if len(xyz) < 3:
            continue
        zs = [p[2] for p in xyz]
        horizontal = max(zs) - min(zs) < tolerance
        if view == "plan":
            if horizontal and abs(zs[0] - elevation) < tolerance:
                primitives.append((0 if not area["opening"] else 1, "opening" if area["opening"] else "floor", xyz))
            elif not horizontal and min(zs) + tolerance < cut < max(zs) - tolerance:
                # Exact intersections of the area edges with the story cut plane.
                intersections = []
                for a, b in zip(xyz, xyz[1:] + xyz[:1], strict=True):
                    if (a[2] <= cut < b[2]) or (b[2] <= cut < a[2]):
                        t = (cut - a[2]) / (b[2] - a[2])
                        intersections.append([a[j] + t * (b[j] - a[j]) for j in range(3)])
                if len(intersections) >= 2:
                    primitives.append((2, "wall_cut", intersections))
        elif not area["opening"]:
            depth = sum(p[0] + p[1] - p[2] if view == "3d" else p[1] for p in xyz) / len(xyz)
            primitives.append((-depth, "floor" if horizontal else "wall", xyz))
    for frame in data["frames"]:
        xyz = [points[p] for p in frame["points"]]
        zs = [p[2] for p in xyz]
        if view == "plan":
            if max(zs) - min(zs) < tolerance and abs(zs[0] - elevation) < tolerance:
                primitives.append((3, "frame", xyz))
            elif min(zs) <= cut <= max(zs) and max(zs) - min(zs) > tolerance:
                t = (cut - zs[0]) / (zs[1] - zs[0])
                primitives.append((4, "column", [[xyz[0][j] + t * (xyz[1][j] - xyz[0][j]) for j in range(3)]]))
        else:
            depth = sum(p[0] + p[1] - p[2] if view == "3d" else p[1] for p in xyz) / len(xyz)
            primitives.append((-depth, "frame", xyz))
    if not primitives:
        raise ValueError("No geometry intersects the requested view")

    def project(p: list[float]) -> tuple[float, float]:
        if view == "plan":
            return p[0], -p[1]
        if view == "elevation":
            return p[0], -p[2]
        return (p[0] - p[1]) * math.sqrt(3) / 2, -(p[0] + p[1]) / 2 - p[2]

    projected = [project(p) for _, _, xyz in primitives for p in xyz]
    min_x, max_x = min(p[0] for p in projected), max(p[0] for p in projected)
    min_y, max_y = min(p[1] for p in projected), max(p[1] for p in projected)
    scale = min((width - 130) / max(max_x - min_x, 1e-6), (height - 160) / max(max_y - min_y, 1e-6))
    supersample = 2 if width * height <= 2_000_000 else 1
    image = Image.new("RGB", (width * supersample, height * supersample), "white")
    draw = ImageDraw.Draw(image)

    def pixel(p: list[float]) -> tuple[float, float]:
        x, y = project(p)
        return (
            (width / 2 + (x - (min_x + max_x) / 2) * scale) * supersample,
            (height / 2 + 10 + (y - (min_y + max_y) / 2) * scale) * supersample,
        )

    if view != "plan":
        image = _solid_image(primitives, pixel, view, image.size, supersample)
        draw = ImageDraw.Draw(image)
    for _, kind, xyz in sorted(primitives, key=lambda item: item[0]) if view == "plan" else []:
        xy = [pixel(p) for p in xyz]
        if kind in {"floor", "wall", "opening"}:
            fill = {"floor": 237, "wall": 191, "opening": 255}[kind]
            draw.polygon(xy, fill=(fill,) * 3, outline=(140,) * 3, width=supersample)
        elif kind == "column":
            x, y = xy[0]
            r = 3 * supersample
            draw.rectangle((x - r, y - r, x + r, y + r), fill=(35,) * 3)
        else:
            draw.line(xy, fill=(45,) * 3, width=(4 if kind == "wall_cut" else 2) * supersample)
    title = {
        "3d": "Vista 3D",
        "plan": f"Planta - {story_name} - Z = {elevation:g} m",
        "elevation": "Elevación frontal",
    }[view]
    draw.text((30 * supersample, 20 * supersample), title, fill=(20,) * 3, font=_font(24 * supersample, True))
    draw.text(
        (30 * supersample, (height - 35) * supersample),
        "Generado desde la API de ETABS. No es una captura de pantalla.",
        fill=(90,) * 3,
        font=_font(15 * supersample),
    )
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), {
        "view": view,
        "story": story_name or None,
        "width": width,
        "height": height,
        "source": "ETABS COM API",
        "is_screenshot": False,
        "desktop_access": False,
        "model_path": data["model_path"],
        "units": "m",
    }


def save_render(png: bytes, view: str) -> Path:
    folder = Path.home() / "Documents" / "ETABS-MCP" / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{view}-{uuid.uuid4().hex}.png"
    with path.open("xb") as stream:
        stream.write(png)
    return path
