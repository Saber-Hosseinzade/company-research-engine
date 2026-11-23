# Dev container image only. The app image is the Dockerfile at the repo root.
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The base image ships a Yarn apt repository whose signing key is no longer valid, so every
# `apt-get update` fails, and with it any feature that installs packages (docker-in-docker).
# Nothing here uses Yarn: Python goes through uv, Node has its own repository.
RUN rm -f /etc/apt/sources.list.d/yarn*.list
