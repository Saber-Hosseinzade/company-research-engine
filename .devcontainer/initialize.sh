#!/bin/sh
# Runs on the host before the container is built (initializeCommand), from the workspace folder.
# Usage: initialize.sh <workspace folder name>
# Creates the folders the container mounts, and the git-ignored PROJECT_CONTEXT.md from
# PROJECT_CONTEXT.template.md (CLAUDE.md imports it).
# Existing files are never overwritten.
set -eu

name=$1
mkdir -p "$HOME/.aws-sandbox" "$HOME/.claude-projects/$name"

# The project record, created once from the template committed in the repo.
if [ ! -e PROJECT_CONTEXT.md ] && [ -f PROJECT_CONTEXT.template.md ]; then
  cp PROJECT_CONTEXT.template.md PROJECT_CONTEXT.md
fi
