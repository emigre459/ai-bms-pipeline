#!/bin/bash
# Hook: runs at the end of every Claude turn.
# Only triggers `make tests` when Python files were modified or created this session.

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Collect modified, staged, and untracked Python files
changed=$(
  {
    git diff --name-only HEAD 2>/dev/null
    git diff --name-only --cached 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null
  } | sort -u | grep '\.py$'
)

if [ -z "$changed" ]; then
  exit 0
fi

echo "Python files changed — running tests before finishing..."
echo "$changed"
echo ""

if ! make tests; then
  # Exit 2 blocks Claude from stopping so it can address the failures
  exit 2
fi
