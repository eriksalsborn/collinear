#!/bin/sh
# Release what is on main:   ./release.sh 0.2.0
#
# People install and update from the `stable` branch, which only ever moves to a release. So main can be mid-change
# without breaking anyone, and a release is the moment they get it. This script:
#   1. checks you are on a clean, pushed main, and runs every test
#   2. tags the commit v0.2.0 and pushes the tag
#   3. moves `stable` to it
#   4. publishes a GitHub release with notes generated from the commits since the last one
set -e
version=$1
[ -n "$version" ] || { echo "usage: ./release.sh 0.2.0"; exit 1; }
cd "$(dirname "$0")"
[ "$(git branch --show-current)" = main ] || { echo "release from main, not $(git branch --show-current)"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first"; exit 1; }
git fetch -q origin
[ "$(git rev-parse main)" = "$(git rev-parse origin/main)" ] || { echo "push main first (or pull): it differs from GitHub's"; exit 1; }
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && { echo "v$version already exists"; exit 1; }

python3 issues.py --test
python3 prs.py --test
python3 test_ui.py

git tag -a "v$version" -m "v$version"
git push -q origin "v$version"
git push -q origin "v$version^{commit}:refs/heads/stable"
gh release create "v$version" --title "v$version" --generate-notes
echo "released v$version: stable now points at it"
