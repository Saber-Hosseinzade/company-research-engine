"""The approval-gate hook: push rule, gh pr merge rule, and the rules that must not change.

Requirement IDs refer to intent/2026-11-23-push-gate/spec.md.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "approval-gate.sh"
NOREPLY = "1+test@users.noreply.github.com"


@pytest.fixture(autouse=True, scope="module")
def _tools_present() -> None:
    # R9: fail, never skip, when the hook's tools are missing.
    assert Path("/usr/bin/jq").exists(), "the approval-gate hook needs jq at /usr/bin/jq"
    assert shutil.which("git"), "the approval-gate hook needs git on PATH"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_env())


def _env(**extra: str) -> dict[str, str]:
    # Isolate from the developer's own git config, so tests behave the same everywhere.
    return {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", **extra}


def make_repo(path: Path, branch: str) -> Path:
    """A repo on `branch` with one commit, a local bare repo as origin, and a noreply email."""
    origin = path.parent / f"{path.name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=_env())
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, env=_env())
    git(path, "config", "user.email", NOREPLY)
    git(path, "config", "user.name", "Test")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    git(path, "remote", "add", "origin", str(origin))
    return path


@pytest.fixture
def feature(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo", "feature/x")


@pytest.fixture
def on_main(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo", "main")


def gate(command: str, repo: Path) -> tuple[str, str]:
    """Run the hook on a Bash call; return (decision, reason), decision one of allow/ask/deny."""
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [str(HOOK)],
        input=call,
        capture_output=True,
        text=True,
        env=_env(CLAUDE_PROJECT_DIR=str(repo)),
        check=True,
    )
    if not result.stdout.strip():
        return "allow", ""
    output = json.loads(result.stdout)["hookSpecificOutput"]
    return str(output["permissionDecision"]), str(output["permissionDecisionReason"])


def decision(command: str, repo: Path) -> str:
    return gate(command, repo)[0]


# R1: settings keep an ask rule for git push and no deny rule.
def test_settings_ask_but_do_not_deny_push() -> None:
    permissions = json.loads((ROOT / ".claude" / "settings.json").read_text())["permissions"]
    assert not any(rule.startswith("Bash(git push") for rule in permissions["deny"])
    assert "Bash(git push:*)" in permissions["ask"]
    assert "Bash(docker push:*)" in permissions["deny"]


# R6 (with R2 detection): pushes of a feature branch ask.
@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin feature/x",
        "git push origin feature/x",
        "git push origin HEAD",
        "git push",
        "git push origin",
        "git push origin main:feature/x",
        "git push origin 'feature/x'",
        "git -c push.default=current push origin feature/x",
        "make check && git push -u origin feature/x",
        "env GIT_TRACE=1 git push origin feature/x",
        "bash -c 'git push origin feature/x'",
    ],
)
def test_push_of_feature_branch_asks(command: str, feature: Path) -> None:
    assert decision(command, feature) == "ask"


# R3: any destination that is main is denied, however it is written.
@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push -u origin main",
        "git push origin feature/x:main",
        "git push origin HEAD:main",
        "git push origin feature/x:refs/heads/main",
        "git push origin :main",
        "git push origin --delete main",
        'git push origin "main"',
        "git push origin 'main'",
        "git push origin ma''in",
        'git push origin feature/x:"main"',
        "git -c k=v push origin main",
        "env git push origin main",
        "cd /tmp && git push origin main",
    ],
)
def test_push_to_main_is_denied(command: str, feature: Path) -> None:
    assert decision(command, feature) == "deny"


# R2: pushes hidden in another shell are still found.
@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'git push origin main'",
        'sh -c "git push origin main"',
        "echo $(git push origin main)",
        "bash -c 'cd x; git push origin main'",
        "/usr/bin/git push origin main",
        "git --no-pager push origin main",
        "git --git-dir /x/.git push origin main",
        "git --work-tree x --git-dir y push origin main",
        "git --namespace n push origin main",
        "git --git-dir=/x/.git push origin main",
        "git push origin feature/x && git push origin main",
        "git push origin feature/x; git push origin main",
        "git push origin feature/x\ngit push origin main",
        "git push origin \\\n  main",
    ],
)
def test_wrapped_push_to_main_is_denied(command: str, feature: Path) -> None:
    assert decision(command, feature) == "deny"


# R4: forced pushes and destinations that can't be read from the text are denied.
@pytest.mark.parametrize(
    "command",
    [
        "git push --all origin",
        "git push --mirror origin",
        "git push --force origin feature/x",
        "git push -f origin feature/x",
        "git push -uf origin feature/x",
        "git push --force-with-lease origin feature/x",
        "git push --force-if-includes origin feature/x",
        "git push origin +feature/x",
        "git push origin 'refs/heads/*:refs/heads/*'",
        "git push origin $B",
        "git push origin feature/x:$B",
        "git push origin `echo main`",
        "git push origin {main,x}",
        "git push origin ma{i,j}n",
        "git push origin ~/x",
    ],
)
def test_forced_or_unreadable_push_is_denied(command: str, feature: Path) -> None:
    assert decision(command, feature) == "deny"


# R5: a bare push resolves where it would go.
@pytest.mark.parametrize("command", ["git push", "git push origin", "git push origin HEAD"])
def test_bare_push_on_main_is_denied(command: str, on_main: Path) -> None:
    assert decision(command, on_main) == "deny"


def test_bare_push_with_upstream_main_is_denied(feature: Path) -> None:
    git(feature, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(feature, "branch", "--set-upstream-to", "origin/main")
    git(feature, "config", "push.default", "upstream")
    assert decision("git push", feature) == "deny"


def test_bare_push_in_detached_head_is_denied(feature: Path) -> None:
    git(feature, "checkout", "-q", "--detach")
    assert decision("git push", feature) == "deny"


def test_bare_push_reads_the_branch_of_the_c_dir(feature: Path, tmp_path: Path) -> None:
    other = make_repo(tmp_path / "other", "main")
    assert decision(f"git -C {other} push", feature) == "deny"
    assert decision(f"git -C {feature} push", feature) == "ask"
    assert decision("git -C ../other push", feature) == "deny"  # relative to the project dir
    assert decision(f"git -C {feature} push origin main", feature) == "deny"


@pytest.mark.parametrize(
    "command",
    ["cd ../other && git push", "cd /tmp; git push origin", "(cd x && git push origin HEAD)"],
)
def test_bare_push_after_cd_is_denied(command: str, feature: Path) -> None:
    # After a cd, the hook can't tell which repo a bare push runs in.
    assert decision(command, feature) == "deny"


def test_named_push_after_cd_asks(feature: Path) -> None:
    assert decision("cd /tmp && git push origin feature/x", feature) == "ask"


def test_heads_main_is_denied(feature: Path) -> None:
    assert decision("git push origin feature/x:heads/main", feature) == "deny"


def test_text_mentioning_a_merge_is_denied(feature: Path) -> None:
    # Spec concern 2: accepted false deny; the workaround is to pass the text in a file.
    assert decision("gh pr create --body 'never run gh pr merge'", feature) == "deny"


# R7: Claude never merges pull requests.
@pytest.mark.parametrize(
    "command",
    ["gh pr merge 1", "gh pr merge 1 --squash", "bash -c 'gh pr merge 1'", "gh  pr  merge"],
)
def test_gh_pr_merge_is_denied(command: str, feature: Path) -> None:
    assert decision(command, feature) == "deny"


# R8: every deny and ask says why.
@pytest.mark.parametrize(
    "command",
    ["git push origin main", "git push -f origin feature/x", "git push origin feature/x", "gh pr merge 1"],
)
def test_decisions_give_a_reason(command: str, feature: Path) -> None:
    result, reason = gate(command, feature)
    assert result in {"ask", "deny"}
    assert reason.strip()


def test_ask_reason_names_the_command(feature: Path) -> None:
    assert "git push origin feature/x" in gate("git push origin feature/x", feature)[1]


def test_merge_reason_says_a_person_merges(feature: Path) -> None:
    assert "a person merges" in gate("gh pr merge 1", feature)[1]


def test_push_to_main_reason_says_what_to_do(feature: Path) -> None:
    assert "Push a feature branch and open a PR" in gate("git push origin main", feature)[1]


# Commands that are not pushes or merges keep their decision.
@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline",
        "echo push main",
        "git pull origin main",
        "gh pr view 1",
        "gh pr create --fill",
        "git commit -m 'push gate'",
    ],
)
def test_commands_without_push_are_allowed(command: str, feature: Path) -> None:
    assert decision(command, feature) == "allow"


# R10: the hook's other rules are unchanged.
def test_commit_on_main_is_still_denied(on_main: Path) -> None:
    assert decision("git commit -m x", on_main) == "deny"


def test_dependency_change_still_asks(feature: Path) -> None:
    assert decision("uv add requests", feature) == "ask"


def test_other_aws_region_is_still_denied(feature: Path) -> None:
    assert decision("aws s3 ls --region us-east-1", feature) == "deny"
