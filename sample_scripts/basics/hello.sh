#!/usr/bin/env bash
# --- scripthub ---
# name: Hello (Bash)
# description: "Bash version: the name arrives as the first positional argument, the greeting as an environment variable. Runs on Linux hosts over SSH, or locally with Git Bash / WSL."
# category: Basics
# parameters:
#   - name: name
#     label: Name
#     type: text
#     default: World
#     required: true
#     pattern: "[A-Za-z0-9 ._-]+"
#     pass_as: positional
#     position: 1
#   - name: greeting
#     label: Greeting
#     type: text
#     default: Hello
#     pass_as: env
#     env_name: GREETING
# --- end scripthub ---
set -eu

name="${1:-World}"
echo "${GREETING:-Hello}, ${name}!"
echo "Running as $(whoami) on $(hostname) ($(uname -s))"
