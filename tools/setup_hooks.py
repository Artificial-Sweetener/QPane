#    QPane - High-performance PySide6 image viewer
#    Copyright (C) 2025  Artificial Sweetener and contributors
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Install git hooks needed for the QPane development workflow."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess


def _git_root() -> Path:
    """Return the repository root detected by git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Error finding git root. Are you in a git repository?"
        ) from exc
    return Path(result.stdout.strip())


def _ensure_hook_directory(git_root: Path) -> Path:
    """Ensure the git hook directory exists and return its path."""
    hook_dir = git_root / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    return hook_dir


def _write_hook(hook_dir: Path, hook_name: str, content: str) -> Path:
    """Write a hook file with consistent encoding and newlines."""
    hook_path = hook_dir / hook_name
    hook_path.write_text(content, encoding="utf-8", newline="\n")
    if os.name != "nt":
        hook_path.chmod(0o755)
    return hook_path


def main() -> int:
    """Install the git hooks for the repository."""
    post_commit_content = """#!/bin/sh
# Local releases are owned by CI; skip semantic-release tagging here.
exit 0
"""

    pre_commit_content = """#!/bin/sh
echo "Running pre-commit checks..."

# Locate executables in the Windows venv
# Note: Git hooks run from the repo root
VENV_SCRIPTS="./.venv/Scripts"
PYTHON="$VENV_SCRIPTS/python.exe"
RUFF="$VENV_SCRIPTS/ruff.exe"
BLACK="$VENV_SCRIPTS/black.exe"

if [ ! -f "$PYTHON" ]; then
    echo "Error: Virtual environment not found at $PYTHON"
    echo "Please set up the environment before committing."
    exit 1
fi

# 1. Run Ruff (Linting & Fixing)
if [ -f "$RUFF" ]; then
    echo "Auto-fixing lint issues with ruff..."
    "$RUFF" check --fix .
    # If ruff failed to fix everything (exit code != 0), we should probably abort
    if [ $? -ne 0 ]; then
        echo "Ruff could not fix all issues. Commit aborted."
        exit 1
    fi
else
    echo "Warning: ruff not found in venv. Skipping."
fi

# 2. Run Black (Formatting)
if [ -f "$BLACK" ]; then
    echo "Auto-formatting with black..."
    "$BLACK" .
else
    echo "Warning: black not found in venv. Skipping."
fi

# 3. Run Custom Tools
echo "Running custom tools..."

# fix_encoding.py (Auto-fix)
if [ -f "tools/fix_encoding.py" ]; then
    echo "Ensuring UTF-8 encoding..."
    "$PYTHON" tools/fix_encoding.py
    if [ $? -ne 0 ]; then
        echo "Encoding check failed. Commit aborted."
        exit 1
    fi
fi

# check_docstrings.py
if [ -f "tools/check_docstrings.py" ]; then
    echo "Checking docstrings..."
    "$PYTHON" tools/check_docstrings.py
    if [ $? -ne 0 ]; then
        echo "Docstring check failed. Commit aborted."
        exit 1
    fi
fi

# check_api_order.py
if [ -f "tools/check_api_order.py" ]; then
    echo "Checking API order..."
    "$PYTHON" tools/check_api_order.py
    if [ $? -ne 0 ]; then
        echo "API order check failed. Commit aborted."
        exit 1
    fi
fi

# check_consistency.py
if [ -f "tools/check_consistency.py" ]; then
    echo "Checking consistency..."
    "$PYTHON" tools/check_consistency.py
    if [ $? -ne 0 ]; then
        echo "Consistency check failed. Commit aborted."
        exit 1
    fi
fi

# add_license_headers.py (Auto-fix)
if [ -f "tools/add_license_headers.py" ]; then
    echo "Ensuring license headers..."
    "$PYTHON" tools/add_license_headers.py
    if [ $? -ne 0 ]; then
        echo "License header check failed. Commit aborted."
        exit 1
    fi
fi

# 4. Stage the changes
# We re-add all modified tracked files to ensure the formatting/fixes are included in the commit.
echo "Staging fixes..."
git add -u

# --- Test Caching ---
# If the staged content (tree) hasn't changed since the last successful test run, skip tests.
CACHE_FILE=".git/hooks/last_passed_tree"
CURRENT_TREE=$(git write-tree)

if [ -f "$CACHE_FILE" ]; then
    LAST_TREE=$(cat "$CACHE_FILE")
    if [ "$CURRENT_TREE" = "$LAST_TREE" ]; then
        echo "Tests passed for this state previously. Skipping."
        exit 0
    fi
fi

# 4. Run Pytest (prefer parallel execution when pytest-xdist is installed)
echo "Running tests in .venv..."
if "$PYTHON" -m pip show pytest-xdist >/dev/null 2>&1; then
    echo "pytest-xdist detected; running pytest -n auto..."
    "$PYTHON" -m pytest -n auto
else
    echo "pytest-xdist not found; running pytest serially."
    "$PYTHON" -m pytest
fi
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "Tests failed! Commit aborted."
    rm -f "$CACHE_FILE"
    exit 1
fi

# Save the tree hash on success
echo "$CURRENT_TREE" > "$CACHE_FILE"

echo "All checks passed."
exit 0
"""

    commit_msg_content = """#!/bin/sh
# The commit message file is passed as the first argument
COMMIT_MSG_FILE=$1
COMMIT_MSG=$(cat "$COMMIT_MSG_FILE")

# Regex for Conventional Commits (Angular style)
# Types: build, chore, ci, docs, feat, fix, perf, refactor, revert, style, test
# Optional scope: (scope)
# Optional breaking change indicator: !
# Colon and space: :
# Subject: Any text
PATTERN="^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\\([a-z0-9\\._-]+\\))?!?: .+$"

# Allow "Merge" commits (automatically generated by git merge)
if echo "$COMMIT_MSG" | grep -q "^Merge"; then
    exit 0
fi

# Check if the message matches the pattern
if ! echo "$COMMIT_MSG" | grep -qE "$PATTERN"; then
    echo "Error: Invalid commit message format."
    echo "------------------------------------------------------------------"
    echo "Your commit message must follow Conventional Commits."
    echo "Examples:"
    echo "  feat: add new login page"
    echo "  fix(auth): handle null token"
    echo "  chore: update dependencies"
    echo "  feat!: breaking change in API"
    echo "------------------------------------------------------------------"
    echo "Your message was:"
    echo "$COMMIT_MSG"
    exit 1
fi
"""

    git_root = _git_root()
    hook_dir = _ensure_hook_directory(git_root)
    _write_hook(hook_dir, "post-commit", post_commit_content)
    _write_hook(hook_dir, "pre-commit", pre_commit_content)
    _write_hook(hook_dir, "commit-msg", commit_msg_content)
    print("All git hooks installed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
