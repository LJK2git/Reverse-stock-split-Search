#!/bin/bash
set -e

DATA_FILES="costs.txt dataset.csv processed.txt secrets.json"

# Back up data files before pulling
for f in $DATA_FILES; do
    if [ -f "/app/$f" ]; then
        cp "/app/$f" "/tmp/$f"
    fi
done

# Pull latest from GitHub
echo "Checking for updates..."
git pull origin main

# Restore data files (don't let git overwrite them)
for f in $DATA_FILES; do
    if [ -f "/tmp/$f" ]; then
        cp "/tmp/$f" "/app/$f"
        echo "Restored $f"
    fi
done

exec python searcher.py
