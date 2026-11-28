"""Host-managed workspace routes for the AgntID MCP tool server."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from time import time

from agentforge.workspace import Workspace, WorkspaceError

_REGISTRY = ".workspace-routes"


def workspace_data_root(default: Path) -> Path:
    return Path(os.getenv("AGENTFORGE_MCP_WORKSPACE_ROOT") or default).expanduser().resolve()


class WorkspaceRoutes:
    """Resolve opaque, short-lived route IDs within one trusted shared directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.registry = self.root / _REGISTRY

    def _route_file(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", workspace_id):
            raise WorkspaceError("Workspace route is invalid or unavailable")
        path = self.registry / workspace_id
        if path.resolve() != path:
            raise WorkspaceError("Workspace route is invalid or unavailable")
        return path

    def _workspace_path(self, path: Path) -> Path:
        if not path.is_relative_to(self.root):
            raise WorkspaceError("Workspace must be below the configured MCP workspace root")
        relative = path.relative_to(self.root)
        if not relative.parts or relative.parts[0].casefold() == _REGISTRY:
            raise WorkspaceError("Workspace must be below the configured MCP workspace root")
        if path.resolve() != path or not path.is_dir():
            raise WorkspaceError("Workspace route target is invalid or unavailable")
        return relative

    def register(self, workspace: Workspace) -> str:
        relative = self._workspace_path(workspace.root)
        self.registry.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace_id = secrets.token_hex(32)
        route = self._route_file(workspace_id)
        with route.open("x", encoding="utf-8") as stream:
            json.dump({"path": relative.as_posix(), "max_file_bytes": workspace.max_file_bytes,
                       "expires_at": time() + 86_400}, stream)
        return workspace_id

    def resolve(self, workspace_id: str, *, max_file_bytes: int) -> Workspace:
        try:
            route = self._route_file(workspace_id)
            data = json.loads(route.read_text(encoding="utf-8"))
            if data["expires_at"] <= time():
                raise ValueError("Expired route")
            relative = Path(data["path"])
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError("Invalid route target")
            workspace = self.root / relative
            self._workspace_path(workspace)
            limit = data["max_file_bytes"]
            if type(limit) is not int or limit < 1:
                raise ValueError("Invalid route file size limit")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkspaceError("Workspace route is invalid or unavailable") from exc
        return Workspace(workspace, max_file_bytes=min(limit, max_file_bytes))

    def revoke(self, workspace_id: str) -> None:
        self._route_file(workspace_id).unlink(missing_ok=True)
