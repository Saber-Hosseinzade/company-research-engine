"""The approval-gate hook's push rule: never to main or forced; any other push asks."""

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "approval-gate.sh"


def decision(command: str, repo: Path) -> str:
    """Run the hook on a Bash call and return its decision: allow, ask or deny."""
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [str(HOOK)],
        input=call,
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(repo), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    if not result.stdout.strip():
        return "allow"
    output = json.loads(result.stdout)["hookSpecificOutput"]
    return str(output["permissionDecision"])


def repo_on(branch: str, tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", branch, str(tmp_path)], check=True)
    return tmp_path


@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin feature/x",
        "git push origin feature/x",
        "git push origin HEAD",
        "git push",
        "git push origin main:feature/x",
        "git -C /some/dir push origin feature/x",
        "make check && git push -u origin feature/x",
    ],
)
def test_push_of_feature_branch_asks(command: str, tmp_path: Path) -> None:
    assert decision(command, repo_on("feature/x", tmp_path)) == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push -u origin main",
        "git push origin feature/x:main",
        "git push origin HEAD:main",
        "git push origin feature/x:refs/heads/main",
        "git push origin :main",
        "git push --all origin",
        "git push --mirror origin",
        "git push --force origin feature/x",
        "git push -f origin feature/x",
        "git push -uf origin feature/x",
        "git push --force-with-lease origin feature/x",
        "git push origin +feature/x",
    ],
)
def test_push_to_main_or_forced_is_denied(command: str, tmp_path: Path) -> None:
    assert decision(command, repo_on("feature/x", tmp_path)) == "deny"


@pytest.mark.parametrize("command", ["git push", "git push origin", "git push origin HEAD"])
def test_push_of_current_branch_on_main_is_denied(command: str, tmp_path: Path) -> None:
    assert decision(command, repo_on("main", tmp_path)) == "deny"


@pytest.mark.parametrize(
    "command",
    ["git status", "git log --oneline", "echo push main", "git pull origin main"],
)
def test_commands_without_push_are_allowed(command: str, tmp_path: Path) -> None:
    assert decision(command, repo_on("feature/x", tmp_path)) == "allow"
