#!/usr/bin/env python3
"""UI test for Collinear (issues.py): plays scripted sessions against fake Linear, git and herdr data with the clock
frozen, and compares every screen, as text, with the ones recorded in ui_test_screens/.

    python3 test_ui.py            run every scenario; lists the screens that changed, exit code 1 if any did
    python3 test_ui.py --update   record the screens as they are now (after a change you meant to make)
    python3 test_ui.py --show two-workspaces 3    print one screen of a scenario as it renders now

Nothing real is touched: no network, no processes, no keychain, and all files go to a temporary folder.
Keys, clicks and the wheel go through the real ScreenWithMouse, so the mouse handling is covered too.
Colours are not compared, only text. Each scenario runs issues.py in a pseudo-terminal of a fixed size."""
import curses, fcntl, json, os, pty, select, shutil, struct, sys, tempfile, termios, time, traceback, types
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORDED_SCREENS_DIR = HERE / "ui_test_screens"
TERMINAL_ROWS = 44
ESC, ENTER = 27, 10
FRAMES_TO_SETTLE_BEFORE_A_KEY = 6  # idle redraws between two keys, so background threads and animations have landed
FROZEN_NOW = datetime(2026, 11, 12, 10, 0, 0)
FROZEN_EPOCH_SECONDS = 1794477610.0  # chosen so Col is neither sniffing nor asleep


# ── what the fake Linear holds ─────────────────────────────────────────────────────────────────────────────────────────

def fake_state(name, state_type, position):
    return {"id": f"s-{name}", "name": name, "type": state_type, "position": position}


FAKE_STATES = [fake_state("Triage", "triage", 0), fake_state("Backlog", "backlog", 1), fake_state("Todo", "unstarted", 2),
               fake_state("In Progress", "started", 3), fake_state("In Review", "started", 4), fake_state("Done", "completed", 5),
               fake_state("Canceled", "canceled", 6)]


def fake_issue(number, title, state_number, priority, days_since_update, assignee="Erik Tester", pull_request=None, description=None):
    return {"id": f"id-{number}", "identifier": f"PLA-{number}", "title": title, "description": description,
            "url": f"https://linear.app/x/issue/PLA-{number}", "branchName": f"erik/pla-{number}-{title.split()[0].lower()}",
            "priority": priority, "updatedAt": f"{FROZEN_NOW - timedelta(days=days_since_update, hours=2):%Y-%m-%dT%H:%M:%S}.000Z",
            "state": FAKE_STATES[state_number],
            "attachments": {"nodes": [{"url": "https://github.com/o/alpha/pull/7", "title": "PR", "metadata": pull_request}] if pull_request else []},
            "assignee": {"id": "me", "displayName": assignee} if assignee else None, "team": {"id": "t1"},
            "project": {"name": "Gateway"}, "cycle": {"number": 4}}


FAKE_ISSUES = [
    fake_issue(1, "First issue about login", 3, 1, 0, description="# Why\n\nLogin **breaks** on `redirect`.\n\n- step one\n- step two\n\n> quoted"),
    fake_issue(2, "Second issue with a workspace", 3, 2, 2,
               pull_request={"status": "inReview", "branch": "erik/pla-2-second", "number": 7, "repoName": "alpha"}),
    fake_issue(3, "Third one in review", 4, 3, 1, pull_request={"status": "open", "draft": True}),
    fake_issue(4, "Fourth sits in todo", 2, 4, 9),
    fake_issue(5, "Fifth in the backlog", 1, 0, 25, assignee=None),
    fake_issue(6, "Sixth needs triage", 0, 0, 3),
]
FAKE_DAILY_REPORT = ("## on your plate\n- **PLA-1 login thing** — PR #7 is in review since Tuesday.\n↳ only you\n"
                     "- **PLA-99 someone else's** — waiting on Sam.\n↳ you and Sam\n- **a loose topic** — nobody filed it yet.\n"
                     "## loose ends\n- reply to Robin about herdr links.\n## today\n- start with PLA-1, it is closest to done.")
ONE_WORKSPACE = [{"label": "PLA-2 Second issue with a workspace", "number": 3, "workspace_id": "w1", "agent_status": "working"}]
TWO_WORKSPACES_FOR_ONE_ISSUE = [
    {"label": "PLA-2 Second issue with a workspace", "number": 3, "workspace_id": "w1", "agent_status": "working", "tab_count": 3,
     "worktree": {"repo_name": "alpha"}},
    {"label": "PLA-2 Second issue, the infra half", "number": 6, "workspace_id": "w2", "agent_status": "idle", "tab_count": 1}]


def fake_linear_graphql(query, variables=None):
    copy_of = lambda value: json.loads(json.dumps(value))
    if "teams {" in query:
        return {"viewer": {"id": "me"}, "teams": {"nodes": [{"id": "t1", "key": "PLA", "name": "Platform"}, {"id": "t2", "key": "APP", "name": "App"}]}}
    if "states" in query:
        wanted = [state for state in FAKE_STATES if f'eq: "{state["type"]}"' in query] if "eq:" in query else FAKE_STATES
        return {"team": {"states": {"nodes": wanted}}}
    if "members" in query:
        return {"viewer": {"id": "me", "displayName": "Erik Tester"},
                "team": {"members": {"nodes": [{"id": "u2", "displayName": "Sam", "active": True}, {"id": "u3", "displayName": "Gone", "active": False}]}}}
    if "projects(" in query:
        return {"projects": {"nodes": [{"id": "p1", "name": "Gateway", "status": {"name": "Started"}, "teams": {"nodes": [{"key": "PLA"}]}}]}}
    if "comments(" in query:
        return {"issue": {"description": "desc", "comments": {"nodes": [
            {"body": "first **comment**", "createdAt": "2026-11-10T08:00:00Z", "user": {"displayName": "Sam"}},
            {"body": "second comment\n\n- with a list", "createdAt": "2026-11-11T08:00:00Z", "user": None}]}}}
    if "issueUpdate" in query or "commentCreate" in query:
        return {"issueUpdate": {"success": True}, "commentCreate": {"success": True}}
    if "assignedIssues" in query:
        return {"viewer": {"assignedIssues": {"nodes": copy_of(FAKE_ISSUES)}}}
    if "issues(first" in query:
        issues_of_view = {"issues": {"nodes": copy_of(FAKE_ISSUES[:4])}}
        return {"team": issues_of_view, "project": issues_of_view}
    if "viewer { id }" in query:
        return {"viewer": {"id": "me"}}
    raise RuntimeError(f"the fake Linear has no answer for: {query[:60]}")


def fake_run_command(*command):
    output = ""
    if command[:1] == ("security",):
        output = "fake-linear-key"
    elif command[:1] == ("git",) and "worktree" in command and str(command[2]).endswith("alpha"):
        output = "worktree /x/alpha-wt/b\nHEAD abc\nbranch refs/heads/erik/pla-2-second\n\n"
    return types.SimpleNamespace(returncode=0, stdout=output, stderr="")


# ── the scenarios: what is pressed, clicked and scrolled ───────────────────────────────────────────────────────────────
# A step is a key (a character or a keycode), or a mouse action:
#   ("click row with", "PLA-4")   press the left button on the screen row that shows that text
#   ("click hint", "?")           press the left button on the footer hint (or dialog corner) that stands for that key
#   ("wheel", "down" | "up")

EVERY_SCREEN = [
    "j", "j", "k", "?", ENTER,                                   # move, the key reference
    " ", "l", "j", "h", ESC,                                     # preview, its comments pane
    "S", "j", ESC, "P", "j", ENTER, "U", "A", ESC,               # status, priority and its undo, assignee
    "/", "p", "l", "a", "-", "2", ENTER, ESC,                    # filter
    "t", ENTER, "h",                                             # a team's issues, and back
    "b", "j", "l", ESC, ESC,                                     # daily report, a topic's actions
    "K", "K", "Z", "Z", "y", "g",                                # hints off/on, Col off/on, copy link, no PR linked
    ENTER, ESC, "n", ESC, "a", "h", "i", ESC, "C", ESC,          # start dialog, new issue, chat with Col, comment
    "D", ESC, "R", "T", ESC, "X", ESC, "G", "5", "k",            # setup, review inbox, projects, cancel, last row, a count
    "t", "/", "a", "p", ESC, ESC,                                # the filter inside a dialog (once crashed the popup)
    "S", "j", ENTER, "U",                                        # a real status change and its undo
    " ", "C", "o", "k", ESC, ESC,                                # the comment composer in the preview
    "B", "B", "Z", "B", "Z", "m", ESC,                           # a bone, twice; with Col off duty; an unbound letter filters
    ("click row with", "PLA-4"), ("click row with", "PLA-4"), ("click hint", ESC),   # select by click, preview by second click, close by its corner
    ("wheel", "down"), ("wheel", "up"), ("click hint", "?"), ENTER,                 # scroll, then a footer hint
    ("click hint", "S"), ("click row with", "In Review"), ("click row with", "In Review"),  # a dialog: click a row, click it again to pick
]
SCENARIOS = {  # name: (terminal columns, the herdr workspaces that are open, the steps)
    "every-screen-wide": (150, ONE_WORKSPACE, EVERY_SCREEN),
    "every-screen-narrow": (100, ONE_WORKSPACE, EVERY_SCREEN),
    "two-workspaces": (150, TWO_WORKSPACES_FOR_ONE_ISSUE, ["j", "j", "w", ESC, " ", ESC, ENTER, ESC, "w", "j", ENTER]),
}


# ── inside the pseudo-terminal: issues.py with everything outside it replaced ──────────────────────────────────────────

class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 12, 10, 0, 0) if tz is None else cls(2026, 11, 12, 9, 0, 0, tzinfo=tz)


class FrozenTime:
    time = staticmethod(lambda: FROZEN_EPOCH_SECONDS)

    def __getattr__(self, name):
        return getattr(time, name)


def replace_the_outside_world(issues, prs, open_workspaces, temporary_home):
    cache_dir = temporary_home / "cache"
    for folder in (cache_dir, temporary_home / "notes", temporary_home / "dev" / "alpha" / ".git", temporary_home / "dev" / "beta" / ".git"):
        folder.mkdir(parents=True)
    issues.CACHE_DIR = cache_dir
    issues.MY_ISSUES_CACHE_FILE = cache_dir / "herdr-issues.json"
    issues.SETUP_PASSED_FILE = cache_dir / "herdr-issues-setup.json"
    issues.DEVELOPMENT_DIR = temporary_home / "dev"
    issues.NOTES_DIR = temporary_home / "notes"
    issues.MY_ISSUES_CACHE_FILE.write_text(json.dumps(FAKE_ISSUES))
    stored_values = {"setup": {"ok": "x"}, "name": {"name": "Erik Tester"}, "digest": f"{FROZEN_NOW:%Y-%m-%d}",
                     "brief": {"date": f"{FROZEN_NOW:%Y-%m-%d}", "text": FAKE_DAILY_REPORT, "steps": ["Linear: get issue"], "cur": 0}}
    for name, value in stored_values.items():
        (cache_dir / f"herdr-issues-{name}.json").write_text(json.dumps(value))
    issues.time = FrozenTime()
    issues.datetime = FrozenDateTime
    issues.linear_graphql = fake_linear_graphql
    issues.run_command = fake_run_command
    issues.worktree_path_for_branch = lambda repository, branch: \
        "/x/alpha-wt/b" if str(repository).endswith("alpha") and branch == "erik/pla-2-second" else None
    issues.open_herdr_workspaces = lambda: open_workspaces
    issues.ask_headless_claude = lambda *arguments, **options: "fake answer"

    def no_processes(*arguments, **options):
        raise RuntimeError("the UI test starts no processes")
    issues.subprocess = types.SimpleNamespace(run=lambda *arguments, **options: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
                                              Popen=no_processes, PIPE=-1, DEVNULL=-3)
    prs.fetch = lambda: {"mine": {"nodes": []}, "review": {"nodes": []}}
    curses.napms = lambda milliseconds: None


class ScriptedWindow:
    """Stands in for the curses window under ScreenWithMouse: draws for real, but input comes from the scenario.
    Right before each step it records the screen. Clicks and the wheel arrive as KEY_MOUSE plus a faked getmouse()."""

    def __init__(self, real_window, issues, steps, recorded_screens):
        self.real_window, self.issues, self.recorded_screens = real_window, issues, recorded_screens
        self.steps_left, self.step_count = list(steps) + ["q"], len(steps) + 1
        self.idle_frames, self.read_blocks_until_a_key = 0, False
        curses.getmouse = lambda: self.mouse_event

    def __getattr__(self, name):
        return getattr(self.real_window, name)

    def timeout(self, milliseconds):
        self.read_blocks_until_a_key = milliseconds == -1
        self.real_window.timeout(milliseconds)

    def screen_text_lines(self):
        height = self.real_window.getmaxyx()[0]
        return [self.real_window.instr(row, 0).decode("utf8", "replace").rstrip() for row in range(height)]

    def position_for(self, mouse_step):
        """(row, column) on screen for a click step, from what is drawn and the click zones registered for it."""
        kind, target = mouse_step
        if kind == "click row with":
            clickable_rows = {zone[0]: zone for zone in self.issues.CLICK_ZONES}  # the newest zone of each row
            row = next((row for row, text in enumerate(self.screen_text_lines()) if target in text and row in clickable_rows), None)
            if row is None:
                raise AssertionError(f"no clickable row shows {target!r}; clickable rows: {sorted(clickable_rows)}; screen:\n"
                                     + "\n".join(f"{n:2} {text}" for n, text in enumerate(self.screen_text_lines()) if text.strip()))
            return row, (clickable_rows[row][1] + clickable_rows[row][2]) // 2
        keycode = ord(target) if isinstance(target, str) else target
        zones_for_key = [zone for zone in self.issues.CLICK_ZONES if zone[3] == [keycode]]
        row, first_column, _, _ = min(zones_for_key, key=lambda zone: zone[2] - zone[1])
        return row, first_column

    def next_scripted_input(self):
        called_from_the_spinner = sys._getframe(4).f_code.co_name == "run_with_spinner"
        if called_from_the_spinner:  # the spinner loop swallows input: never feed it the scenario
            time.sleep(0.005)
            return -1
        self.idle_frames += 1
        time.sleep(0.01)
        if self.read_blocks_until_a_key:  # screen.timeout(-1): a real read would wait for the key
            self.idle_frames = FRAMES_TO_SETTLE_BEFORE_A_KEY
        if self.idle_frames < FRAMES_TO_SETTLE_BEFORE_A_KEY or not self.steps_left:
            return -1
        self.idle_frames = 0
        step = self.steps_left.pop(0)
        step_number = self.step_count - len(self.steps_left)
        self.recorded_screens.append((f"{step_number:03d} before {step!r}", "\n".join(self.screen_text_lines())))
        if isinstance(step, tuple):
            if step[0] == "wheel":
                self.mouse_event = (0, 0, 0, 0, self.issues.WHEEL_DOWN if step[1] == "down" else curses.BUTTON4_PRESSED)
            else:
                row, column = self.position_for(step)
                self.mouse_event = (0, column, row, 0, curses.BUTTON1_PRESSED)
            return curses.KEY_MOUSE
        return ord(step) if isinstance(step, str) else step

    def getch(self):
        return self.next_scripted_input()

    def get_wch(self):
        return self.next_scripted_input()


def play_scenario_inside_the_terminal(scenario_name, output_file):
    _, open_workspaces, steps = SCENARIOS[scenario_name]
    sys.path.insert(0, str(HERE))
    import issues, prs
    temporary_home = Path(tempfile.mkdtemp(prefix="collinear-ui-test-"))
    recorded_screens, error_text = [], ""
    try:
        replace_the_outside_world(issues, prs, open_workspaces, temporary_home)
        curses.wrapper(lambda real_window: issues.main(ScriptedWindow(real_window, issues, steps, recorded_screens)))
    except SystemExit:
        pass
    except BaseException:
        error_text = traceback.format_exc()
    finally:
        shutil.rmtree(temporary_home, ignore_errors=True)
    ending = f"\nCRASHED\n{error_text}" if error_text else "\nfinished cleanly\n"
    Path(output_file).write_text("".join(f"\n########## {label}\n{text}\n" for label, text in recorded_screens) + ending)


# ── outside: run each scenario in a pseudo-terminal and compare ────────────────────────────────────────────────────────

def play_scenario(scenario_name):
    """The scenario's screens as one text, played in a pseudo-terminal of the scenario's size."""
    columns = SCENARIOS[scenario_name][0]
    output_file = Path(tempfile.mkstemp(prefix="collinear-ui-test-", suffix=".txt")[1])
    child, terminal = pty.fork()
    if child == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--inside-terminal", scenario_name, str(output_file)])
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", TERMINAL_ROWS, columns, 0, 0))
    give_up_at = time.time() + 180
    while time.time() < give_up_at and os.waitpid(child, os.WNOHANG) == (0, 0):
        if select.select([terminal], [], [], 0.2)[0]:
            try:
                os.read(terminal, 65536)
            except OSError:
                break
    screens = output_file.read_text() or "\nCRASHED\nthe scenario wrote nothing (it hung or was killed)\n"
    output_file.unlink()
    return screens


def screens_by_label(screens_text):
    labelled = {}
    for block in screens_text.split("\n########## ")[1:]:
        label, _, text = block.partition("\n")
        labelled[label] = text
    return labelled


def main(arguments):
    if arguments[:1] == ["--inside-terminal"]:
        return play_scenario_inside_the_terminal(arguments[1], arguments[2])
    if arguments[:1] == ["--show"]:
        wanted_step = f"{int(arguments[2]):03d} "
        for label, text in screens_by_label(play_scenario(arguments[1])).items():
            if label.startswith(wanted_step):
                print(f"{label}\n{text}")
        return 0
    should_record = arguments == ["--update"]
    RECORDED_SCREENS_DIR.mkdir(exist_ok=True)
    something_failed = False
    for scenario_name in SCENARIOS:
        now, recording = play_scenario(scenario_name), RECORDED_SCREENS_DIR / f"{scenario_name}.txt"
        if "\nCRASHED\n" in now:
            something_failed = True
            print(f"✗ {scenario_name}: the popup crashed\n{now[now.index('CRASHED'):]}")
        elif should_record or not recording.exists():
            recording.write_text(now)
            print(f"● {scenario_name}: recorded {len(screens_by_label(now))} screens")
        elif now == recording.read_text():
            print(f"✓ {scenario_name}: {len(screens_by_label(now))} screens as recorded")
        else:
            something_failed = True
            before, after = screens_by_label(recording.read_text()), screens_by_label(now)
            changed = [label for label in after if before.get(label) != after[label]] + [label for label in before if label not in after]
            print(f"✗ {scenario_name}: {len(changed)} of {len(after)} screens differ from the recording:")
            for label in changed[:12]:
                rows_before, rows_after = before.get(label, "").split("\n"), after.get(label, "").split("\n")
                first_row = next((row for row in range(max(len(rows_before), len(rows_after)))
                                  if rows_before[row:row + 1] != rows_after[row:row + 1]), 0)
                print(f"    {label}  (first difference on row {first_row})")
                print(f"      was: {''.join(rows_before[first_row:first_row + 1]).strip()[:150]}")
                print(f"      now: {''.join(rows_after[first_row:first_row + 1]).strip()[:150]}")
            print(f"    meant to change? record them with: python3 {Path(__file__).name} --update")
    return 1 if something_failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
