"""One locked-down headless Claude Code call, through the Claude Code command line and your own Claude login.

Shared by Shortlist (reading a LinkedIn page you copied) and Tailor (reading postings and rewriting your points).
Claude runs with no tools, no MCP servers, no slash commands and no saved session, in an empty folder, with only
the given rules as its system prompt, so the text it reads can't make it do anything but return JSON.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODEL = "sonnet"
EFFORT = "medium"
TIMEOUT_S = 90


class ClaudeError(Exception):
    """Claude couldn't answer: not installed, signed out, offline, timed out, or an answer in the wrong shape."""


def claude_path(profile: dict | None = None) -> str | None:
    return (profile or {}).get("claude_path") or shutil.which("claude") or next(
        (str(p) for p in Path.home().glob(".nvm/versions/node/*/bin/claude")), None)


def call(system: str, message: str, schema: dict, claude: str, model: str = MODEL, effort: str = EFFORT,
         timeout: float = TIMEOUT_S, error=ClaudeError):
    """`system` as the system prompt, `message` on stdin, the answer as JSON in `schema`'s shape. Raises `error`
    (ClaudeError or a subclass) when there's no usable answer."""
    cmd = [claude, "-p", "--model", model, "--effort", effort, "--system-prompt", system,  # only the rules, not a coding agent's
           "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
           "--output-format", "json", "--json-schema", json.dumps(schema)]
    # your Claude Code settings may set a high effort for your own sessions (CLAUDE_CODE_EFFORT_LEVEL), which would
    # beat --effort and keep each call thinking for minutes; these calls always run at `effort`
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_EFFORT", "MAX_THINKING_TOKENS")}
    env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    with tempfile.TemporaryDirectory() as empty:  # an empty folder: no project files or instructions to pick up
        try:
            run = subprocess.run(cmd, input=message, capture_output=True, text=True, timeout=timeout, cwd=empty, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise error(f"Claude didn't answer ({exc.__class__.__name__}).") from exc
    try:
        result = json.loads(run.stdout) if (run.stdout or "").strip() else {}
    except ValueError:
        result = {"is_error": True, "result": run.stdout}
    if not isinstance(result, dict) or not result:
        result = {"is_error": True, "result": (run.stderr or "").strip() or f"it stopped with code {run.returncode}"}
    if result.get("is_error"):
        msg = str(result.get("result") or "unknown error")
        if "auth" in msg.lower() or "login" in msg.lower() or "oauth" in msg.lower():
            raise error("Claude Code isn't signed in. Run `claude auth login` in Terminal once.")
        raise error(f"Claude couldn't rewrite: {msg[:160]}")
    data = result.get("structured_output")
    if data is None:  # no structured output: read the JSON object in the text, even inside a code fence
        text = str(result.get("result") or "")
        found = re.search(r"\{.*\}", text, flags=re.S)
        try:
            data = json.loads(found.group(0) if found else text)
        except ValueError as exc:
            raise error("Claude's answer wasn't in the expected shape.") from exc
    return data
