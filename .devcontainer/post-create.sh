#!/usr/bin/env bash
# Dev container setup. VS Code runs this once, after the container is created
# (postCreateCommand in devcontainer.json).
#
# Stop at the first failure, so a half-built environment can't pass as finished:
#   -e  exit when a command fails
#   -u  treat an unset variable as an error
#   -o pipefail  a failure anywhere in a pipeline fails the whole pipeline
set -euo pipefail

# Docker creates the config volumes as root, and ~/.config with them.
sudo chown -R vscode:vscode /home/vscode/.claude /home/vscode/.config/gh /home/vscode/.cache/pre-commit
sudo chown vscode:vscode /home/vscode/.config /home/vscode/.cache

# uv manages Python dependencies. Pinned, so a rebuild gets the same version.
pip install -q --user uv==0.13.6

# The virtualenv lives outside the bind-mounted workspace (UV_PROJECT_ENVIRONMENT,
# set in devcontainer.json), so a .venv created on the host can't collide with it.
# Creates uv.lock on the first run; afterwards installs exactly what it pins.
if [ -f uv.lock ]; then
  ~/.local/bin/uv sync --frozen
else
  ~/.local/bin/uv sync
fi

# Git hooks: ruff, shell checks and a secret scan run on every commit.
~/.local/bin/uv run pre-commit install

# Git identity is set per repo, never globally: the noreply address keeps the
# personal email out of public history. Check before the first commit.
if [ -z "$(git config --local user.email 2>/dev/null || true)" ]; then
  echo "NOTE: set the repo-local Git identity before committing:"
  echo "  git config user.name  '<your name>'"
  echo "  git config user.email '<id>+<username>@users.noreply.github.com'"
fi
