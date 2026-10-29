"""Explicit companion-skill installation; never runs at MCP server startup."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import uuid
from pathlib import Path

from etabs_mcp.version import SERVER_VERSION

SKILL_NAME = "etabs-mcp-api"
API_ONLY_POLICY = (
    "NEVER use Computer Use, computer-use, computeruse, screen control or desktop automation under any circumstances "
    "for ETABS tasks, including setup, troubleshooting, views, screenshots and recovery. "
    "Never use mouse, keyboard, OCR, window/menu/dialog automation, or equivalent scripts, other MCPs or delegates. "
    "Use ETABS APIs only. If unsupported, report the limit; never request permission to use Computer Use. "
)


def companion_skill_text() -> str:
    return importlib.resources.files("etabs_mcp").joinpath("agent_skill", "SKILL.md").read_text(encoding="utf-8")


def install_agent_skill(destination: str | Path | None = None) -> dict:
    """Install our named skill only; preserve edits, unrelated files and backups."""
    folder = (
        Path(destination).expanduser() if destination is not None else Path.home() / ".agents" / "skills" / SKILL_NAME
    )
    folder = folder.absolute()
    if folder.is_symlink():
        raise ValueError("Skill destination is a symlink; it was not changed")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "SKILL.md"
    marker = folder / ".etabs-mcp.json"
    if target.is_symlink() or marker.is_symlink():
        raise ValueError("Skill files are symlinks; they were not changed")
    content = companion_skill_text().encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    prior = target.read_bytes() if target.exists() else None
    backup = None
    if prior is not None and prior != content:
        try:
            owned = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("An existing skill is not managed by ETABS MCP; it was preserved") from exc
        if owned.get("name") != SKILL_NAME or owned.get("sha256") != hashlib.sha256(prior).hexdigest():
            raise ValueError("The installed skill has local edits; it was preserved. Use a new --skill-dir")
        backups = folder / ".backups"
        backups.mkdir(exist_ok=True)
        backup = backups / f"SKILL-{uuid.uuid4().hex}.md"
        with backup.open("xb") as stream:
            stream.write(prior)
    if prior != content:
        temporary = folder / f".SKILL-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    metadata = {"name": SKILL_NAME, "version": SERVER_VERSION, "sha256": digest}
    marker.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "success": True,
        "skill": SKILL_NAME,
        "path": str(target),
        "version": SERVER_VERSION,
        "changed": prior != content,
        "backup": str(backup) if backup else None,
        "desktop_access": False,
        "client_tools_disabled": False,
    }
