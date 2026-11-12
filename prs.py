#!/usr/bin/env python3
"""Open PRs picker for a herdr popup. j/k move, enter opens in browser, r refresh, q quits."""
import curses, json, locale, re, subprocess, sys, threading
from pathlib import Path
from datetime import datetime, timezone

QUERY = """
fragment F on PullRequest { number title url headRefName body updatedAt reviewDecision mergeable repository { nameWithOwner }
  commits(last:1){nodes{commit{statusCheckRollup{state}}}} }
{ mine: search(query:"is:pr is:open author:@me sort:updated-desc", type:ISSUE, first:50){nodes{...F}}
  review: search(query:"is:pr is:open review-requested:@me sort:updated-desc", type:ISSUE, first:50){nodes{...F}} }"""

CACHE = Path.home() / ".cache" / "herdr-prs.json"
SECTIONS = [("mine", "MY OPEN PRS"), ("review", "REVIEW REQUESTED")]
GREEN, YELLOW, RED, CYAN = 1, 2, 3, 4
DOTS = {GREEN: "●", YELLOW: "◐", RED: "✗"}  # shape as well as colour, so states differ without relying on hue
# Which Linear workspace and which team keys are yours is kept out of the code, in local_settings.json next to this
# file (not in git; see local_settings.example.json). Without it, PRs simply have no Linear link.
LOCAL_SETTINGS_FILE = Path(__file__).parent / "local_settings.json"
LOCAL_SETTINGS = json.loads(LOCAL_SETTINGS_FILE.read_text()) if LOCAL_SETTINGS_FILE.exists() else {}
LINEAR_WORKSPACE = LOCAL_SETTINGS.get("linear_workspace", "")  # the "acme" of https://linear.app/acme
LINEAR_TEAM_KEYS = LOCAL_SETTINGS.get("linear_team_keys", [])  # ["ENG", "OPS"]: update when a Linear team is added
# the folder your git repositories are cloned into; both popups look for a PR's or an issue's repository there
REPOSITORIES_FOLDER = Path(LOCAL_SETTINGS.get("repositories_folder", "~/Development")).expanduser()


def linear_id_pattern(team_keys):
    """Matches an issue id of one of these teams (ENG-123), in any case: branch names are lower case."""
    return re.compile(r"\b(?:%s)-\d+\b" % "|".join(map(re.escape, team_keys)), re.I)


LINEAR_ID = linear_id_pattern(LINEAR_TEAM_KEYS) if LINEAR_TEAM_KEYS else None
AGES = [(7, "this week"), (30, "more than 1 week old"), (None, "more than 1 month old")]


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


def open_branch(repo, branch, label):
    """Fetch the PR branch and open it in a brand-new herdr workspace (closing the popup), or return an error."""
    if (out := run_command("git", "-C", str(repo), "fetch", "origin", branch)).returncode:
        return f"fetch failed: {out.stderr.strip()[-120:]}"
    path = worktree_path_for_branch(repo, branch)
    if not path:
        # ponytail: worktrees live next to the repo as <repo>-wt/<branch>
        path = f"{repo}-wt/{branch.replace('/', '-')}"
        if (out := run_command("git", "-C", str(repo), "worktree", "add", path, branch)).returncode:
            return f"worktree failed: {out.stderr.strip()[-120:]}"
    spaces = open_herdr_workspaces()
    if ws := next((s for s in spaces if s["label"] == label and s.get("worktree", {}).get("checkout_path") == path), None):
        run_command("herdr", "workspace", "focus", ws["workspace_id"])
        sys.exit()
    if (out := run_command("herdr", "workspace", "create", "--cwd", path, "--label", label, "--focus")).returncode:
        return f"workspace failed: {(out.stderr or out.stdout).strip()[-120:]}"
    sys.exit()


def remove_worktree(repo, path, branch):
    """Remove a worktree made by open_branch, its local branch and its workspaces.
    git refuses on uncommitted changes, and keeps the branch if it has commits not pushed."""
    if (out := run_command("git", "-C", path, "worktree", "remove", path)).returncode:
        return f"remove failed: {out.stderr.strip()[-120:]}"
    for ws in open_herdr_workspaces():
        if ws.get("worktree", {}).get("checkout_path") == path:
            run_command("herdr", "workspace", "close", ws["workspace_id"])
    # -d, not -D: only deletes when everything is pushed to the upstream
    if (out := run_command("git", "-C", str(repo), "branch", "-d", branch)).returncode:
        return f"removed worktree, kept branch {branch}: {out.stderr.strip().splitlines()[0][:100]}"
    return f"removed worktree and branch {branch}"


def status(pr):
    return state(pr)[0]


def time_since_text(updated_at, now):
    secs = int((now - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


def age_bucket(updated_at, now):
    days = (now - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).days
    return next(label for limit, label in AGES if limit is None or days < limit)


def fetch():
    """Raw PR data from GitHub (~4s: checks + mergeable are slow to compute), also saved to CACHE."""
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}"], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    data = json.loads(out.stdout)["data"]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data))
    return data


def build_rows(data):
    rows = []  # (header, None) or (None, pr)
    for key, title in SECTIONS:
        rows.append((title, None))
        prs = [dict(pr, mine=key == "mine") for pr in data[key]["nodes"] if pr.get("number")]
        now = datetime.now(timezone.utc)
        for _, label in AGES:
            group = [pr for pr in prs if age_bucket(pr["updatedAt"], now) == label]
            if group:
                rows.append(("", None))
                rows.append((f"  ── {label} ({len(group)})", None))
                rows += [(None, pr) for pr in group]
        if not prs:
            rows.append(("  (none)", None))
        rows.append(("", None))
    return rows


def is_group(text):
    return bool(text) and text.startswith("  ──")


def visible(rows, query):
    """Rows whose PR matches query (repo, number, title); drops age groups left empty."""
    q = query.lower()
    keep = [(t, pr) for t, pr in rows if pr is None or
            q in f"{pr['repository']['nameWithOwner']} #{pr['number']} {pr['title']}".lower()]
    out = []
    for i, (t, pr) in enumerate(keep):
        if is_group(t) and (i + 1 == len(keep) or keep[i + 1][1] is None):
            if out and out[-1] == ("", None):
                out.pop()
            continue
        out.append((t, pr))
    return out


def jump(picks, rows, sel, direction):
    """Index into picks of the first PR in the next/previous age group ({ and })."""
    group = lambda i: max((j for j in range(picks[i] + 1) if rows[j][1] is None and rows[j][0]), default=0)
    if direction > 0:
        return next((i for i in range(sel, len(picks)) if group(i) != group(sel)), sel)
    first = next(i for i in range(len(picks)) if group(i) == group(sel))
    if first != sel:
        return first
    return next((i for i in range(len(picks)) if group(i) == group(max(sel - 1, 0))), 0)


def main(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.set_escdelay(25)
    # 256-colour: vivid green vs orange, the theme's green/yellow are too close to tell apart
    green, yellow = (46, 208) if curses.COLORS >= 256 else (curses.COLOR_GREEN, curses.COLOR_YELLOW)
    for n, c in ((GREEN, green), (YELLOW, yellow), (RED, curses.COLOR_RED), (CYAN, curses.COLOR_CYAN)):
        curses.init_pair(n, c, -1)

    # Show the cached list instantly, refresh from GitHub in the background.
    bg = {"data": None, "error": "", "busy": False}

    def refresh():
        if bg["busy"]:
            return
        bg["busy"] = True

        def work():
            try:
                bg["data"] = fetch()
            except Exception as e:  # gh missing, offline, not logged in…
                bg["error"] = f"refresh failed: {str(getattr(e, 'stderr', '') or e).strip()[:120]}"
            bg["busy"] = False
        threading.Thread(target=work, daemon=True).start()

    scr.timeout(250)  # redraw while waiting for keys so refreshed data shows up

    def actions(pr):
        """Actions menu for one PR. Returns a message for the footer."""
        name = f"{pr['repository']['nameWithOwner'].split('/')[-1]} #{pr['number']}"
        linear = linear_url(pr)
        repo = REPOSITORIES_FOLDER / pr["repository"]["nameWithOwner"].split("/")[-1]
        options = []  # (key, label)
        if linear:
            options += [("l", "open Linear issue"), ("o", "copy Linear link")]
        if repo.is_dir() and pr.get("headRefName"):
            options.append(("b", "open branch as workspace"))
            wt = worktree_path_for_branch(repo, pr["headRefName"])
            if wt and wt.startswith(f"{repo}-wt/"):  # only worktrees b made, never the main checkout
                options.append(("x", "remove worktree, branch + workspace"))
        if pr["mine"]:
            options += [("c", "close PR"), ("d", "close PR + delete branch")]
        lines = [f" {name}  {pr['title']}", ""] + [f"  {k}   {label}" for k, label in options]
        if not linear:
            lines.append("  (no Linear issue found)")
        lines += ["", "  j/k · enter · h/esc cancel"]
        h, w = scr.getmaxyx()
        bw = min(max(len(l) for l in lines) + 4, w - 2)
        box = curses.newwin(len(lines) + 2, bw, max((h - len(lines)) // 2 - 1, 0), max((w - bw) // 2, 0))
        box.box()
        cur, keys = 0, {ord(k) for k, _ in options}
        while True:
            for y, line in enumerate(lines):
                attr = curses.A_BOLD if y == 0 else curses.A_REVERSE if options and y == cur + 2 else 0
                box.addnstr(y + 1, 1, line.ljust(bw - 2), bw - 2, attr)
            box.refresh()
            key = scr.getch()
            if key in (27, ord("q"), ord("h")):
                return ""
            if key in keys:
                break
            if key in (10, 13, curses.KEY_ENTER) and options:
                key = ord(options[cur][0])
                break
            if key in (ord("j"), curses.KEY_DOWN):
                cur = min(cur + 1, len(options) - 1)
            elif key in (ord("k"), curses.KEY_UP):
                cur = max(cur - 1, 0)
        doing = {ord("b"): "fetching branch…", ord("x"): "removing worktree…", ord("c"): "closing PR…", ord("d"): "closing PR + deleting branch…"}
        if key in doing:  # gh/git take seconds, show it's working instead of the "esc cancel" line
            box.addnstr(len(lines), 1, f"  {doing[key]}".ljust(bw - 2), bw - 2, curses.color_pair(CYAN) | curses.A_BOLD)
            box.refresh()
        if key == ord("l"):
            subprocess.run(["open", linear])
            sys.exit()
        if key == ord("b"):
            return open_branch(repo, pr["headRefName"], pr["title"])
        if key == ord("x"):
            return remove_worktree(repo, wt, pr["headRefName"])
        if key == ord("o"):
            subprocess.run(["pbcopy"], input=linear, text=True)
            sys.exit()  # closes the popup; curses.wrapper restores the terminal
        cmd = ["gh", "pr", "close", pr["url"]] + (["--delete-branch"] if key == ord("d") else [])
        out = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if out.returncode:
            return f"failed: {(out.stderr or out.stdout).strip().splitlines()[-1]}"
        return f"closed {name}" + (" and deleted its branch" if key == ord("d") else "")

    try:
        all_rows = build_rows(json.loads(CACHE.read_text()))
    except (OSError, ValueError, KeyError):
        all_rows = [("  loading PRs…", None)]
    refresh()
    query, searching, count, pending_g, sel, msg = "", False, "", False, 0, ""
    while True:
        if bg["data"]:
            all_rows, bg["data"] = build_rows(bg["data"]), None
        if bg["error"]:
            msg, bg["error"] = bg["error"], ""
        rows = visible(all_rows, query)
        picks = [i for i, (_, pr) in enumerate(rows) if pr]
        sel = min(sel, max(len(picks) - 1, 0))
        scr.erase()
        now = datetime.now(timezone.utc)
        h, w = scr.getmaxyx()
        top = max(0, (picks[sel] if picks else 0) - h + 3)
        for y, (text, pr) in enumerate(rows[top:top + h - 1]):
            i = top + y
            if pr is None:
                if is_group(text):  # age group: cyan rule across the width
                    scr.addnstr(y, 0, f"{text} ".ljust(w, "─"), w - 1, curses.color_pair(CYAN) | curses.A_BOLD)
                elif text and not text.startswith("  ("):  # top section: full-width bar
                    scr.addnstr(y, 0, f" {text} ".ljust(w - 1), w - 1, curses.A_REVERSE | curses.A_BOLD)
                else:
                    scr.addnstr(y, 0, text, w - 1, curses.A_DIM)
                continue
            attr = curses.A_REVERSE if picks and i == picks[sel] else 0
            colour, label = state(pr)
            repo = f"{pr['repository']['nameWithOwner'].split('/')[-1]} #{pr['number']}"
            scr.addstr(y, 2, DOTS[colour], curses.color_pair(colour))
            scr.addnstr(y, 3, f" {label:<16}", max(w - 4, 0),
                        attr | curses.color_pair(colour) | (curses.A_BOLD if colour == GREEN else 0))
            scr.addnstr(y, 20, f"{time_since_text(pr['updatedAt'], now):>8}  ", max(w - 21, 0), attr | curses.A_DIM)
            scr.addnstr(y, 30, f"{repo:<30} {pr['title']}".ljust(w - 31), max(w - 31, 0), attr)
        if msg:
            scr.addnstr(h - 1, 0, msg, w - 1, curses.A_BOLD)
        elif searching or query:
            hint = "   enter keep · esc clear" if searching else f"   ({len(picks)} match{'es' * (len(picks) != 1)}) · esc clear"
            scr.addnstr(h - 1, 0, f"/{query}{'▏' if searching else ''}{hint}", w - 1, curses.A_BOLD)
        else:
            x = 0
            for text, attr in (("⟳ refreshing…  " if bg["busy"] else "", curses.color_pair(CYAN) | curses.A_BOLD),
                               ("j/k · 5j · gg/G · ^d/^u · {/} group · / search · enter open · l actions… · r refresh · q quit   ", curses.A_DIM),
                               (DOTS[GREEN], curses.color_pair(GREEN)), (" approved  ", curses.A_DIM),
                               (DOTS[YELLOW], curses.color_pair(YELLOW)), (" pending  ", curses.A_DIM),
                               (DOTS[RED], curses.color_pair(RED)), (" failing", curses.A_DIM)):
                if x < w - 1:
                    scr.addnstr(h - 1, x, text, w - 1 - x, attr)
                x += len(text)
        key = scr.getch()
        if key == -1:
            continue
        msg = ""

        if searching:
            if key == 27:
                query, searching = "", False
            elif key in (10, 13, curses.KEY_ENTER):
                searching = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
            elif 32 <= key < 127:
                query, sel = query + chr(key), 0
            continue

        n = int(count or 1)
        if not (ord("1") <= key <= ord("9") or (key == ord("0") and count)):
            count = ""
        was_g, pending_g = pending_g, False
        if ord("1") <= key <= ord("9") or (key == ord("0") and count):
            count += chr(key)
        elif key == ord("q"):
            return
        elif key == 27:
            if not query:
                return
            query = ""
        elif key == ord("/"):
            searching, query, sel = True, "", 0
        elif not picks:
            if key == ord("r"):
                refresh()
        elif key in (ord("j"), curses.KEY_DOWN):
            sel = min(sel + n, len(picks) - 1)
        elif key in (ord("k"), curses.KEY_UP):
            sel = max(sel - n, 0)
        elif key == ord("g"):
            sel, pending_g = (0, False) if was_g else (sel, True)
        elif key == ord("G"):
            sel = len(picks) - 1
        elif key == 4:  # ctrl-d
            sel = min(sel + max(h // 2, 1), len(picks) - 1)
        elif key == 21:  # ctrl-u
            sel = max(sel - max(h // 2, 1), 0)
        elif key == ord("}"):
            sel = jump(picks, rows, sel, 1)
        elif key == ord("{"):
            sel = jump(picks, rows, sel, -1)
        elif key in (10, 13, curses.KEY_ENTER):
            subprocess.run(["open", rows[picks[sel]][1]["url"]])
        elif key == ord("l"):
            msg = actions(rows[picks[sel]][1])
            if msg.startswith("closed"):
                refresh()
        elif key == ord("r"):
            refresh()


def _test():
    pr = lambda rd, m, c: {"reviewDecision": rd, "mergeable": m, "commits": {"nodes": [{"commit": {"statusCheckRollup": c and {"state": c}}}]}}
    assert status(pr("APPROVED", "MERGEABLE", "SUCCESS")) == GREEN
    assert status(pr("APPROVED", "UNKNOWN", None)) == GREEN
    assert status(pr("REVIEW_REQUIRED", "MERGEABLE", "SUCCESS")) == YELLOW
    assert status(pr("APPROVED", "MERGEABLE", "PENDING")) == YELLOW
    assert status(pr("APPROVED", "MERGEABLE", "FAILURE")) == RED
    assert status(pr("APPROVED", "CONFLICTING", "SUCCESS")) == RED
    assert status(pr("CHANGES_REQUESTED", "MERGEABLE", "SUCCESS")) == RED
    assert status({"reviewDecision": None, "mergeable": "MERGEABLE", "commits": {"nodes": []}}) == YELLOW
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert age_bucket("2026-11-25T00:00:00Z", now) == "this week"
    assert age_bucket("2026-11-20T00:00:00Z", now) == "more than 1 week old"
    assert age_bucket("2026-10-01T00:00:00Z", now) == "more than 1 month old"
    mk = lambda n, t: {"repository": {"nameWithOwner": "o/repo"}, "number": n, "title": t}
    assert build_rows({"mine": {"nodes": [{}]}, "review": {"nodes": []}})[1] == ("  (none)", None)
    rows = [("MINE", None), ("", None), ("  ── this week (2)", None), (None, mk(1, "fix login")), (None, mk(2, "add cache")),
            ("", None), ("  ── more than 1 month old (1)", None), (None, mk(3, "old login bug")), ("", None)]
    assert [pr["number"] for _, pr in visible(rows, "LOGIN") if pr] == [1, 3]
    assert not any(is_group(t) and "month" in t for t, _ in visible(rows, "cache"))
    assert visible(rows, "") == rows
    picks = [i for i, (_, pr) in enumerate(rows) if pr]
    assert jump(picks, rows, 0, 1) == 2 and jump(picks, rows, 2, -1) == 0 and jump(picks, rows, 1, -1) == 0
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
    if sys.argv[1:] == ["--test"]:
        _test()
    else:
        locale.setlocale(locale.LC_ALL, "")
        curses.wrapper(main)
