"""
MCP server definition — tools, lifespan, and ASGI app factory.

Exposes MCP tools:
- ``discover_api``    — lists available skills and usage guidance
- ``read_skills``     — returns requested skill content
- ``list_instances``  — lists running ETABS instances
- ``get_status``      — reports connection health
- ``prepare_session`` — opens ETABS and prepares a saved empty model without computer use
- ``execute_code``    — runs validated Python against the ETABS COM bridge
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations

from etabs_mcp.connection import ETABSInstance, InstanceRegistry, connect_and_run
from etabs_mcp.environment import ensure_windows_environment
from etabs_mcp.file_io.helpers import (
    detect_input_output_collision,
    get_allowed_dirs,
    get_input_data,
    write_output_file,
)
from etabs_mcp.file_io.path_validator import FileIOError
from etabs_mcp.sandbox.executor import Executor
from etabs_mcp.session import prepare_etabs_session
from etabs_mcp.skills import SkillsManager
from etabs_mcp.status import read_model_status
from etabs_mcp.version import SERVER_VERSION, check_version_warning

logger = logging.getLogger(__name__)


def _load_api_index() -> list[dict]:
    """Load the pre-built ETABS API index JSON."""
    import sys

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "etabs_api_index.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "etabs_mcp" / "etabs_api_index.json")
    candidates.append(Path(__file__).parent / "etabs_api_index.json")

    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                entries = json.load(f)
            logger.info("ETABS API index loaded: %d entries from %s", len(entries), p)
            return entries
    return []


# ETABS method name abbreviations → full English words (expand in corpus so user
# queries with full words hit abbreviated method names like GetDispl, BaseReact)
_CORPUS_EXPAND: dict[str, list[str]] = {
    "displ": ["displacement"],
    "react": ["reaction"],
    "accel": ["acceleration"],
    "vel": ["velocity"],
    "coord": ["coordinate", "coordinates"],
    "prop": ["property", "properties"],
    "matl": ["material"],
    "stiff": ["stiffness"],
    "freq": ["frequency"],
    "patt": ["pattern"],
    "combo": ["combination"],
    "rot": ["rotation"],
    "tors": ["torsion"],
    "defl": ["deflection"],
    "nonlin": ["nonlinear"],
    "gen": ["generalized", "general"],
    "pier": ["pier", "wall"],
    "spandrel": ["spandrel", "beam", "wall"],
    "grav": ["gravity"],
    "seism": ["seismic"],
    "resp": ["response"],
    "spec": ["spectrum"],
    "hist": ["history"],
    "sect": ["section"],
    "axi": ["axial"],
    "vert": ["vertical"],
    "horiz": ["horizontal"],
    "pts": ["point", "points"],
    "obj": ["object"],
    "elm": ["element"],
    "def": ["definition", "default"],
    "num": ["number", "count"],
    "param": ["parameter"],
    "conc": ["concrete"],
    "st": ["steel"],
    "reb": ["rebar", "reinforcement"],
    "lin": ["linear"],
    "nonprismatic": ["nonprismatic", "tapered", "variable"],
    "rs": ["response", "spectrum"],
    "th": ["time", "history"],
    "lc": ["load", "case"],
    "lp": ["load", "pattern"],
}

# Reverse: user query full words → ETABS abbreviation tokens
_QUERY_EXPAND: dict[str, list[str]] = {
    "displacement": ["displ"],
    "reaction": ["react"],
    "acceleration": ["accel"],
    "velocity": ["vel"],
    "coordinate": ["coord"],
    "coordinates": ["coord"],
    "property": ["prop"],
    "properties": ["prop"],
    "material": ["matl", "mat"],
    "stiffness": ["stiff"],
    "frequency": ["freq"],
    "pattern": ["patt"],
    "combination": ["combo"],
    "rotation": ["rot"],
    "torsion": ["tors"],
    "deflection": ["defl"],
    "nonlinear": ["nonlin"],
    "generalized": ["gen"],
    "section": ["sect"],
    "concrete": ["conc"],
    "steel": ["st"],
    "rebar": ["reb"],
    "reinforcement": ["reb"],
    "linear": ["lin"],
    "element": ["elm", "obj"],
    "object": ["obj"],
    "point": ["pts", "pt"],
    "joint": ["pt", "pts", "point"],
    "node": ["pt", "pts", "point"],
    "wall": ["pier", "spandrel", "area"],
    "gravity": ["grav"],
    "seismic": ["seism"],
    "response": ["resp"],
    "spectrum": ["spec"],
    "history": ["hist"],
    "axial": ["axi", "axial"],
    "vertical": ["vert"],
    "horizontal": ["horiz"],
    "tapered": ["nonprismatic"],
    "variable": ["nonprismatic"],
    "frame": ["frame", "beam", "column", "brace"],
    "beam": ["beam", "frame"],
    "column": ["column", "frame"],
    "slab": ["slab", "area"],
    "shell": ["shell", "area"],
}


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens — handles camelCase, dots, underscores."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z0-9]+", text.lower())


def _expand_corpus(tokens: list[str]) -> list[str]:
    """Add full-word synonyms alongside ETABS abbreviation tokens."""
    extra: list[str] = []
    for t in tokens:
        if t in _CORPUS_EXPAND:
            extra.extend(_CORPUS_EXPAND[t])
    return tokens + extra


def _expand_query(tokens: list[str]) -> list[str]:
    """Add ETABS abbreviation tokens alongside full user-query words."""
    extra: list[str] = []
    for t in tokens:
        if t in _QUERY_EXPAND:
            extra.extend(_QUERY_EXPAND[t])
    return tokens + extra


def _build_bm25(entries: list[dict]) -> Any:
    """Build a BM25 index over the API entries."""
    try:
        from rank_bm25 import BM25Okapi
        corpus = [
            _expand_corpus(_tokenize(
                f"{e.get('title','')} {e.get('method','')} {e.get('description','')} "
                f"{e.get('qualified_name','')} {e.get('python_call','')} "
                f"{' '.join(p['name'] for p in e.get('params', []))}"
            ))
            for e in entries
        ]
        return BM25Okapi(corpus)
    except Exception as exc:
        logger.warning("BM25 index build failed: %s", exc)
        return None


_API_ENTRIES: list[dict] = []
_BM25_INDEX: Any = None

try:
    _API_ENTRIES = _load_api_index()
    _BM25_INDEX = _build_bm25(_API_ENTRIES)
except Exception as _e:
    logger.warning("Could not load ETABS API index: %s", _e)


def _register_tools(
    mcp: FastMCP,
    registry: InstanceRegistry,
    exc: Executor,
    skills_mgr: SkillsManager,
    args_allowed_dirs: list[Path],
) -> None:
    """Register MCP tools on *mcp*, closing over the *InstanceRegistry*."""

    def _resolve_target(instance: str | None) -> ETABSInstance:
        instances = registry.get_active_instances()
        if not instances:
            raise ValueError("No ETABS instances found. Is ETABS running?")
        if instance is None:
            if len(instances) > 1:
                aliases = [i.alias for i in instances]
                raise ValueError(f"Multiple instances running — specify one: {aliases}")
            return instances[0]
        pid = registry.resolve(instance)
        if pid is None:
            alive = [i.alias for i in instances]
            raise ValueError(f"{instance!r} is unknown. Available: {alive}")
        matches = [i for i in instances if i.alias == instance and i.pid == pid]
        if not matches:
            alive = [i.alias for i in instances]
            raise ValueError(f"{instance!r} is no longer running. Available: {alive}")
        return matches[0]

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Discover API and skills",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def discover_api() -> str:
        """Discover available API guidance and skills.

        Call this FIRST before using other etabs-mcp tools.
        Then use ``read_skills`` with one or more specific skill names to load full guidance.
        """
        return (
            "## Start without desktop automation\n\n"
            "Call prepare_session when ETABS is closed or on its Start Page. "
            "For a new building use new_model=True and the requested stories/grid. "
            "Use the returned alias in execute_code. Existing models are protected. "
            "Do not use computer-use for modelling. View layout is manual: the COM "
            "API only refreshes existing windows.\n\n" + skills_mgr.discover_api()
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Read ETABS skills",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def read_skills(skills: list[str]) -> str:
        """Read one or more skills by name.

        Use ``discover_api`` first to list available skills.
        Each skill provides domain-specific guidance (e.g. analysis, geometry, loads).

        Parameters
        ----------
        skills: list[str]
            List of skill names to read.  Use ``discover_api`` to see available skills.
        """
        return skills_mgr.read_skills(skills)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="List running ETABS instances",
            readOnlyHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def list_instances() -> list[dict[str, Any]]:
        """List all running ETABS instances.

        Returns a list of instances with their alias, process ID, currently
        open file path, and ETABS version.  Call this before ``execute_code``
        when multiple ETABS instances may be running.  The ``alias``
        (e.g. ``etabs1``) is stable for the server session.

        If a version is below the minimum supported (21.0.0), a ``warning``
        field is included with details.
        """
        return [inst.asdict() for inst in registry.get_active_instances()]

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get ETABS instance status",
            readOnlyHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def get_status(instance: str | None = None) -> dict[str, Any]:
        """Check the connection to an ETABS instance.

        Pass ``instance`` (alias from ``list_instances``) to target a
        specific instance.  Omit it when only one instance is running.

        Returns connection state, ETABS version, model path, and lock status.
        """
        try:
            target = _resolve_target(instance)
        except ValueError as e:
            return {"connected": False, "model_ready": False, "error": str(e)}

        def _read_status(model: Any) -> dict[str, Any]:
            result = read_model_status(model)
            result.update(server_version=SERVER_VERSION, alias=target.alias, pid=target.pid)
            warning = check_version_warning(result["etabs_version"])
            if warning:
                result["warning"] = warning
            return result

        try:
            return connect_and_run(_read_status, target.pid, timeout=10.0)
        except TimeoutError:
            return {"connected": False, "model_ready": False, "alias": target.alias,
                    "pid": target.pid, "error": f"{target.alias} (PID {target.pid}): connection timed out"}
        except Exception as e:
            return {"connected": False, "model_ready": False, "alias": target.alias,
                    "pid": target.pid, "error": f"{target.alias} (PID {target.pid}): {e}"}

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Open ETABS and prepare a model without desktop automation",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def prepare_session(
        instance: str | None = None,
        new_model: bool = False,
        stories: int = 1,
        story_height: float = 3.0,
        first_story_height: float = 3.0,
        grid_lines_x: int = 2,
        grid_lines_y: int = 2,
        spacing_x: float = 6.0,
        spacing_y: float = 6.0,
    ) -> dict[str, Any]:
        """Open ETABS if needed and prepare a usable session, without computer-use.

        Call this before creating geometry on the Start Page or when ETABS is closed.
        For a NEW building use new_model=True (omit instance): it opens a separate
        ETABS process, creates the requested grid in metres, and saves a unique
        model. Existing files and unsaved geometry are never reset or overwritten.
        With new_model=False a loaded model is returned unchanged; an empty,
        unsaved session receives a new grid and unique file automatically.
        This tool does not click, type, arrange other apps, or create view windows.
        If ETABS needs authentication, the user completes that sign-in manually.
        """
        try:
            return prepare_etabs_session(
                registry, instance, new_model, stories, story_height, first_story_height,
                grid_lines_x, grid_lines_y, spacing_x, spacing_y,
            )
        except Exception as exc:
            return {"success": False, "computer_use_required": False, "error": str(exc)}

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Python code",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    async def execute_code(
        ctx: Context,
        code: str,
        instance: str | None = None,
        input_data_path: str | None = None,
        output_data_path: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Execute Python code in a sandbox against the ETABS COM API (call discover_api and read_skills first).

        The sandbox provides a pre-connected ``model`` variable (``SapModel``) and optional ``input_data``
        (if ``input_data_path`` is provided), plus ``json`` and ``math`` modules.
        ``import`` statements, ``dir()``, ``getattr()``, and dunder access are **BLOCKED**.

        The last expression value or an explicit ``result = ...`` assignment is returned.

        Parameters
        ----------
        code: str
            Python source code to execute. Use ``model`` (SapModel) to interact with ETABS.
        instance: str, optional
            Alias from ``list_instances`` (e.g. ``etabs1``). Omit when only one instance runs.
        input_data_path: str, optional
            Path to a ``.csv`` or ``.xlsx`` file. Content is injected as immutable ``input_data``.
        output_data_path: str, optional
            Path to write the ``result`` value as ``.csv`` or ``.xlsx``.
            ``result`` must be a list-of-lists (flat) or dict of sheet dicts (multi-sheet).
        overwrite: bool, optional
            Allow overwriting an existing output file.
        """
        try:
            target = _resolve_target(instance)
        except ValueError as e:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"{e} Use prepare_session to open ETABS and prepare a model without computer-use.",
                "duration_seconds": 0.0,
            }

        allowed_dirs = await get_allowed_dirs(ctx, args_allowed_dirs, input_data_path, output_data_path)

        try:
            await detect_input_output_collision(input_data_path, output_data_path, allowed_dirs)
            input_data, _ = await get_input_data(input_data_path, allowed_dirs)
        except FileIOError as e:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"{e.code}: {e.message}",
                "duration_seconds": 0.0,
            }

        def _run(model: Any) -> dict[str, Any]:
            status = read_model_status(model)
            if not status["model_ready"]:
                return {
                    "success": False, "result": None, "stdout": "", "stderr": "",
                    "error": "ETABS has no ready model. Call prepare_session before execute_code; computer-use is not required.",
                    "duration_seconds": 0.0, "requires_session_preparation": True,
                }
            return exc.execute(code, model, input_data=input_data).to_dict()

        try:
            result = connect_and_run(_run, target.pid, timeout=600.0)
        except TimeoutError:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"{target.alias} (PID {target.pid}): code execution timed out",
                "duration_seconds": 0.0,
            }
        except Exception as e:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"{target.alias} (PID {target.pid}): {e}",
                "duration_seconds": 0.0,
            }

        if output_data_path is not None and result.get("success"):
            try:
                result["result"] = write_output_file(
                    output_data_path, result["result"], allowed_dirs, overwrite=overwrite
                )
            except FileIOError as e:
                return {
                    "success": False,
                    "result": None,
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "error": f"{e.code}: {e.message}",
                    "duration_seconds": result.get("duration_seconds", 0.0),
                }

        if target.warning:
            result["warning"] = target.warning
        return result


    @mcp.tool(
        annotations=ToolAnnotations(
            title="Search ETABS API documentation",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def search_docs(query: str, top_k: int = 5) -> list[dict]:
        """Search ETABS API documentation by natural language query.

        Returns matching API methods with their Python calling convention.
        Use this to discover the correct method name, parameters, and return
        format for any ETABS operation before using execute_code.

        Parameters
        ----------
        query: str
            Natural language description of what you want to do, e.g.
            "get joint displacement", "assign frame section", "run modal analysis"
        top_k: int
            Number of results to return (default 5, max 20).
        """
        if not _API_ENTRIES:
            return [{"error": "API index not available. Run build_api_index.py first."}]

        top_k = min(top_k, 20)
        tokens = _expand_query(_tokenize(query))

        # BM25 search
        if _BM25_INDEX is not None:
            try:
                scores = _BM25_INDEX.get_scores(tokens)
                top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
                return [
                    {
                        "score": round(float(scores[i]), 3),
                        "method": f"{_API_ENTRIES[i]['class']}.{_API_ENTRIES[i]['method']}",
                        "description": _API_ENTRIES[i].get("description", ""),
                        "python_call": _API_ENTRIES[i].get("python_call", ""),
                        "cs_signature": _API_ENTRIES[i].get("cs_signature", ""),
                        "params": _API_ENTRIES[i].get("params", []),
                    }
                    for i in top_indices
                    if scores[i] > 0
                ]
            except Exception as exc:
                logger.warning("BM25 search failed: %s -- falling back to keyword", exc)

        # Simple keyword fallback
        scored = []
        for e in _API_ENTRIES:
            entry_tokens = set(_expand_corpus(_tokenize(
                f"{e.get('title','')} {e.get('method','')} {e.get('description','')} "
                f"{e.get('qualified_name','')} {' '.join(p['name'] for p in e.get('params', []))}"
            )))
            score = sum(1 for k in tokens if k in entry_tokens)
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "score": s,
                "method": f"{e['class']}.{e['method']}",
                "description": e.get("description", ""),
                "python_call": e.get("python_call", ""),
                "cs_signature": e.get("cs_signature", ""),
            }
            for s, e in scored[:top_k]
        ]


def create_mcp_server(allowed_dirs: list[Path], fastmcp_kwargs: dict | None = None) -> FastMCP:
    """Create an MCP server instance with tools registered."""
    ensure_windows_environment()
    fastmcp_kwargs = fastmcp_kwargs or {}

    registry = InstanceRegistry()

    @lifespan
    async def mcp_lifespan(server: Any) -> AsyncIterator[None]:
        logger.info("ETABS MCP server started (%d API entries indexed)", len(_API_ENTRIES))
        yield

    mcp = FastMCP(
        "ETABS MCP",
        version=SERVER_VERSION,
        instructions=(
            "This MCP server bridges AI agents to CSI ETABS via the ETABS COM API. "
            "For a new building call prepare_session(new_model=True, stories=...) before execute_code. "
            "If ETABS is closed or shows its Start Page, call prepare_session. "
            "Do not use computer-use, screenshots, mouse or keyboard to create or modify models. "
            "The COM API refreshes existing views but does not create/arrange view windows; "
            "explain this limit instead of falling back to desktop automation. "
            "Use `discover_api` first to list available skills and guidance, then call "
            "`read_skills` with skill names to load detailed instructions. "
            "Use `list_instances` to see running ETABS instances, "
            "`execute_code` to run code against a live ETABS model (the `model` variable "
            "is the pre-connected SapModel object), and `get_status` to check connection. "
            "When a `warning` field appears in any tool response, report it to the user."
        ),
        lifespan=mcp_lifespan,
        **fastmcp_kwargs,
    )
    _register_tools(mcp, registry, Executor(), SkillsManager(), args_allowed_dirs=allowed_dirs)
    return mcp
