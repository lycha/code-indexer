#!/bin/bash
set -e

cd /Users/kjackowski/IdeaProjects/ai-os/code-indexer

# Install the package in editable mode
pip install -e . 2>/dev/null || python3 -m pip install -e .

# Verify installation
python3 -c "import indexer; print('indexer package importable')" 2>/dev/null || echo "Package not yet created - will be created by T1"

echo "Init complete"
