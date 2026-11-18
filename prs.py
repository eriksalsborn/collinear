#!/usr/bin/env python3
"""What Collinear (issues.py) knows about pull requests and this machine: the GitHub fetch, a PR's state, its Linear
link, the local settings, and a few git and herdr helpers. It was a popup of its own once; my PRs in Collinear replaced it."""
import json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

QUERY = """
fragment F on PullRequest { number title url headRefName body updatedAt isDraft reviewDecision mergeable repository { nameWithOwner }
  commits(last:1){nodes{commit{statusCheckRollup{state}}}} }
{ mine: search(query:"is:pr is:open author:@me sort:updated-desc", type:ISSUE, first:50){nodes{...F}}
  review: search(query:"is:pr is:open review-requested:@me sort:updated-desc", type:ISSUE, first:50){nodes{...F}} }"""

CACHE = Path.home() / ".cache" / "herdr-prs.json"
GREEN, YELLOW, RED = 1, 2, 3
# Which Linear workspace and which team keys are yours is kept out of the code, in local_settings.json next to this
# file (not in git; see local_settings.example.json). Without it, PRs simply have no Linear link.
LOCAL_SETTINGS_FILE = Path(__file__).parent / "local_settings.json"
LOCAL_SETTINGS = json.loads(LOCAL_SETTINGS_FILE.read_text()) if LOCAL_SETTINGS_FILE.exists() else {}


def save_local_setting(name, value):
    """Keep one setting in local_settings.json, leaving the others as they are."""
    LOCAL_SETTINGS[name] = value
    LOCAL_SETTINGS_FILE.write_text(json.dumps(LOCAL_SETTINGS, indent=2) + "\n")


LINEAR_WORKSPACE = LOCAL_SETTINGS.get("linear_workspace", "")  # the "acme" of https://linear.app/acme
LINEAR_TEAM_KEYS = LOCAL_SETTINGS.get("linear_team_keys", [])  # ["ENG", "OPS"]: update when a Linear team is added
# the folder your git repositories are cloned into; Collinear looks for a PR's or an issue's repository there
REPOSITORIES_FOLDER = Path(LOCAL_SETTINGS.get("repositories_folder", "~/Development")).expanduser()


def linear_id_pattern(team_keys):
    """Matches an issue id of one of these teams (ENG-123), in any case: branch names are lower case."""
    return re.compile(r"\b(?:%s)-\d+\b" % "|".join(map(re.escape, team_keys)), re.I)


LINEAR_ID = linear_id_pattern(LINEAR_TEAM_KEYS) if LINEAR_TEAM_KEYS else None


def state(pr):
    """(colour, label) shown for a PR."""
    checks = (pr["commits"]["nodes"] or [{}])[0].get("commit", {}).get("statusCheckRollup") or {}
    checks = checks.get("state")
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        return RED, "✗ changes req."
    if checks in ("FAILURE", "ERROR"):
        return RED, "✗ checks failed"
    if pr["mergeable"] == "CONFLICTING":
        return RED, "✗ conflict"
    # ponytail: mergeable UNKNOWN (GitHub computes it lazily) counts as fine
    if pr["reviewDecision"] == "APPROVED":
        return (GREEN, "✓ APPROVED") if checks in ("SUCCESS", None) else (YELLOW, "approved, CI…")
    return YELLOW, "CI running" if checks in ("PENDING", "EXPECTED") else "needs review"


def linear_url(pr):
    """Linear issue URL from the first issue ID in branch, title or body (how Linear links PRs), else None."""
    if not (LINEAR_ID and LINEAR_WORKSPACE):
        return None
    for text in (pr.get("headRefName"), pr["title"], pr.get("body")):
        if m := LINEAR_ID.search(text or ""):
            return f"https://linear.app/{LINEAR_WORKSPACE}/issue/{m.group().upper()}"
    return None


def run_command(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)  # never the terminal: a child reading stdin would eat keystrokes


def worktree_path_for_branch(repo, branch):
    """Folder where branch is checked out (main checkout or a worktree), else None."""
    listing = run_command("git", "-C", str(repo), "worktree", "list", "--porcelain").stdout
    return next((b.splitlines()[0][9:] for b in listing.split("\n\n") if f"branch refs/heads/{branch}\n" in b + "\n"), None)


def open_herdr_workspaces():
    return json.loads(run_command("herdr", "workspace", "list").stdout or "{}").get("result", {}).get("workspaces", [])


def time_since_text(updated_at, now):
    secs = int((now - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


def fetch():
    """Raw PR data from GitHub (~4s: checks + mergeable are slow to compute), also saved to CACHE."""
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}"], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    data = json.loads(out.stdout)["data"]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data))
    return data


def _test():
    colour_of = lambda pull_request: state(pull_request)[0]
    pr = lambda rd, m, c: {"reviewDecision": rd, "mergeable": m, "commits": {"nodes": [{"commit": {"statusCheckRollup": c and {"state": c}}}]}}
    assert colour_of(pr("APPROVED", "MERGEABLE", "SUCCESS")) == GREEN
    assert colour_of(pr("APPROVED", "UNKNOWN", None)) == GREEN
    assert colour_of(pr("REVIEW_REQUIRED", "MERGEABLE", "SUCCESS")) == YELLOW
    assert colour_of(pr("APPROVED", "MERGEABLE", "PENDING")) == YELLOW
    assert colour_of(pr("APPROVED", "MERGEABLE", "FAILURE")) == RED
    assert colour_of(pr("APPROVED", "CONFLICTING", "SUCCESS")) == RED
    assert colour_of(pr("CHANGES_REQUESTED", "MERGEABLE", "SUCCESS")) == RED
    assert colour_of({"reviewDecision": None, "mergeable": "MERGEABLE", "commits": {"nodes": []}}) == YELLOW
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert state(pr("APPROVED", "MERGEABLE", "SUCCESS")) == (GREEN, "✓ APPROVED")
    assert state(pr("APPROVED", "MERGEABLE", "PENDING"))[1] == "approved, CI…"
    assert state(pr("REVIEW_REQUIRED", "MERGEABLE", "SUCCESS"))[1] == "needs review"
    assert time_since_text("2026-11-30T00:00:00Z", now) == "just now"
    assert time_since_text("2026-11-29T21:00:00Z", now) == "3h ago"
    assert time_since_text("2026-11-27T00:00:00Z", now) == "3d ago"
    assert time_since_text("2026-11-01T00:00:00Z", now) == "4w ago"
    global LINEAR_ID, LINEAR_WORKSPACE
    lin = lambda b, t, body: {"headRefName": b, "title": t, "body": body}
    LINEAR_ID, LINEAR_WORKSPACE = linear_id_pattern(["ENG", "OPS"]), "acme"
    assert linear_url(lin("sam/eng-123-fix", "x", "")) == "https://linear.app/acme/issue/ENG-123"
    assert linear_url(lin("main", "fix utf-8 bug", "Closes OPS-7")) == "https://linear.app/acme/issue/OPS-7"
    assert linear_url({"title": "no issue", "headRefName": None, "body": None}) is None
    LINEAR_ID = None
    assert linear_url(lin("sam/eng-123-fix", "x", "")) is None, "no local settings: no Linear link"
    print("ok")


if __name__ == "__main__":
    _test()  # python3 prs.py --test; there is nothing else to run here
