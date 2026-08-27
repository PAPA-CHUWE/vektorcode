"""Git tool for version control operations within the workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import get_workspace


def _resolve_safe(path: str) -> Path:
    """Resolve *path* and ensure it stays inside the workspace."""
    ws = get_workspace()
    target = (ws / path).resolve()
    if not str(target).startswith(str(ws)):
        raise PermissionError(f"Path escapes workspace: {path}")
    return target


def git_tool(
    subcommand: str,
    path: str = ".",
    staged: bool = False,
    max_count: int | None = None,
    message: str | None = None,
    branch_name: str | None = None,
    switch_only: bool = False,
) -> dict:
    """Execute a git subcommand and return structured results."""
    try:
        ws = get_workspace()
        if not ws.exists():
            return {"success": False, "stdout": "", "stderr": "Workspace does not exist", "exit_code": 1}
        
        safe_path = None
        if path and path != ".":
            safe_path = _resolve_safe(path)
        
        cmd = ["git"]
        if subcommand == "status":
            cmd.append("status")
        elif subcommand == "diff":
            cmd.append("diff")
            if staged:
                cmd.append("--staged")
            if safe_path:
                cmd.append("--")
                cmd.append(str(safe_path.relative_to(ws)))
        elif subcommand == "log":
            cmd.append("log")
            if max_count is not None:
                cmd.extend(["--max-count", str(max_count)])
            cmd.append("--oneline")
        elif subcommand == "add":
            cmd.append("add")
            if safe_path:
                cmd.append(str(safe_path.relative_to(ws)))
            else:
                cmd.append(".")
        elif subcommand == "commit":
            if not message:
                return {"success": False, "stdout": "", "stderr": "commit message is required", "exit_code": 1}
            check = subprocess.run(
                ["git", "diff", "--staged", "--quiet"],
                cwd=str(ws),
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                return {"success": False, "stdout": "", "stderr": "No staged changes to commit", "exit_code": 1}
            cmd.append("commit")
            cmd.append("-m")
            cmd.append(message)
        elif subcommand == "branch":
            cmd.append("branch")
            if branch_name:
                if switch_only:
                    cmd.append("switch")
                    cmd.append(branch_name)
                else:
                    cmd.append("switch")
                    cmd.append("-c")
                    cmd.append(branch_name)
            else:
                cmd.append("--list")
        elif subcommand == "push":
            cmd.append("push")
        elif subcommand == "pull":
            cmd.append("pull")
        elif subcommand == "merge":
            cmd.append("merge")
            if not branch_name:
                return {"success": False, "stdout": "", "stderr": "branch name required for merge", "exit_code": 1}
            cmd.append(branch_name)
        else:
            return {"success": False, "stdout": "", "stderr": f"Unknown subcommand: {subcommand}", "exit_code": 1}
        
        timeout = 30 if subcommand in ("push", "pull", "merge") else 10
        result = subprocess.run(
            cmd,
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Git command timed out", "exit_code": 1}
    except PermissionError as e:
        return {"success": False, "stdout": "", "stderr": str(e), "exit_code": 1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "exit_code": 1}


def handler(**kwargs):
    """Handler for the tool registry."""
    allowed = {
        "subcommand", "path", "staged", "max_count", "message", "branch_name", "switch_only"
    }
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    result = git_tool(**filtered)
    return result

