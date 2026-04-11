#!/bin/bash
# Quick local lint check — run before committing
set -e

echo "=== Ruff check ==="
ruff check custom_components/ tests/ --fix

echo "=== Ruff format ==="
ruff format custom_components/ tests/

echo "=== All clean! ==="
