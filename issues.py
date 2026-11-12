#!/usr/bin/env python3
"""My open Linear issues for a herdr popup, opencode-styled.
enter: repo -> worktree on Linear's branch -> phase -> claude in a fresh tab of the issue's workspace. ? lists every key.
Phases hand off through notes files in ~/.claude/work/<ID>/; prompts live in prompts/*.md."""
import curses, json, locale, re, shlex, subprocess, sys, textwrap, threading, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import NamedTuple
from prs import run_command, worktree_path_for_branch, open_herdr_workspaces, time_since_text, REPOSITORIES_FOLDER  # same folder, python puts it on sys.path

OPEN_STATE_TYPES = ["started", "unstarted", "backlog", "triage"]  # shown, in this order
ISSUE_FIELDS = """id identifier title description url branchName priority updatedAt state { id name type position }
  attachments { nodes { url title metadata } } assignee { id displayName } team { id }
  project { name } cycle { number }"""
MY_ISSUES_QUERY = ("{ viewer { assignedIssues(first: 100, filter: {state: {type: {in: %s}}}) { nodes { %s } } } }"
         % (json.dumps(OPEN_STATE_TYPES), ISSUE_FIELDS))
CACHE_DIR = Path.home() / ".cache"
MY_ISSUES_CACHE_FILE = CACHE_DIR / "herdr-issues.json"
NOTES_DIR = Path.home() / ".claude" / "work"  # outside the worktree, so removing the worktree keeps the notes
PROMPTS_DIR = Path(__file__).parent / "prompts"
NO_REPOSITORY_CHOICE = "∅  no repo · just the issue, no code"
PHASES = [("issue", "write the issue: what, why, acceptance criteria"),
          ("plan", "think (Col grills you) → discover → spec"),
          ("build", "build it from the spec"), ("review", "fresh eyes on the diff vs the spec")]
ACCENT, MUTED, SELECTED_ROW, RED, BORDER, CODE, GREEN, YELLOW, ORANGE, PURPLE, SELECTED_ROW_MARKER, TITLE_TAG, DIM, DEFAULT_TEXT, DONE_FLASH, COL_FACE, COL_ACCENT, COL_BADGE, COL_BADGE_QUIET_TEXT = range(1, 20)
# Linear-style priority bars + word + colour: the bars still read on the selected row, where everything is one colour.
# Only single-width symbols: "‼" renders 2 columns wide in some terminals and shifts the rest of the row.
PRIORITY_DISPLAY = {1: ("!!!", "urgent", RED), 2: ("▮▮▮", "high", ORANGE), 3: ("▮▮▯", "medium", YELLOW), 4: ("▮▯▯", "low", MUTED)}
# Linear's status glyphs, by state type
ICON_BY_STATE_TYPE = {"triage": ("◈", ORANGE), "backlog": ("◌", DIM), "unstarted": ("○", MUTED), "started": ("◐", YELLOW),
              "completed": ("●", PURPLE), "canceled": ("⊘", DIM)}
STATE_POSITION_BY_NAME, STARTED_STATE_POSITIONS = {}, []  # state name -> position, and the started states' positions in order; learned from the issues seen


def progress_icon_for_state(name, typ):
    """Linear's progress circle: the started states each get their slice (◔ in progress … ◕ in review), filled
    and coloured by how far along they are; done is a full purple dot, backlog a dashed one."""
    if typ == "started" and name in STATE_POSITION_BY_NAME and len(STARTED_STATE_POSITIONS) > 1:
        f = (STARTED_STATE_POSITIONS.index(STATE_POSITION_BY_NAME[name]) + 1) / (len(STARTED_STATE_POSITIONS) + 1)
        return ("◔" if f < 0.4 else "◐" if f < 0.6 else "◕"), (YELLOW if f < 0.5 else GREEN)
    return ICON_BY_STATE_TYPE.get(typ, ("○", MUTED))
# a linked PR's state, most in need of attention first; with several PRs the row shows the first that applies
PULL_REQUEST_BADGES = {"conflict": ("✗ conflict", RED), "draft": ("◌ draft", MUTED), "review": ("● review", YELLOW),
            "open": ("● open", GREEN), "linked": ("● PR", MUTED), "merged": ("✓ merged", PURPLE), "closed": ("✗ closed", MUTED)}


class UiState:
    """What every screen shares about the screen itself. Plain class attributes: read and assigned as UiState.name."""
    draw_screen_behind_dialog = None  # the list screen's draw(): dialogs repaint the list behind them, so no stale dialog stays on screen
    grey_out_behind_dialog = False  # grey out everything behind a dialog (setup with something missing)
    title_of_open_dialog = None  # a new title plays the open animation once
    footer_hints_visible = True  # K toggles the key hints in the footer; ? always stays
    col_is_visible = True  # Z toggles Col off and on; remembered in the cache dir
    col_is_working = False  # Col is on a job (report, answer, draft): he looks around, sniffs, and says what he's at
    lookups_col_is_working_on = []  # the lookups of that job so far
    guided_tour_step = None  # the step of the tour Col is waiting on (see GUIDED_TOUR_STEPS); None when no tour is running
    tour_closing_card_is_due = False  # the last step was just done: the list shows the closing card next


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
UNDO_STACK = []  # (issue, input to send, fields to put back, label): what u reverts, newest last
FAILED_CHANGE_MESSAGES = []  # background mutations that Linear rejected, shown in the footer
ASSIGNEE_CHIP_COLOURS = (ACCENT, GREEN, YELLOW, ORANGE, PURPLE, CODE)  # assignee chip colours, hashed from the name
# Collinear, "Col": the border collie that herds your issues. Lives in the corner, drops tips, cheers you on.
# Four lines, single-width glyphs only; the bubble sits on line 1 (its eyes).
COL_FACES = {"calm": ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ◉ ▐▌ ◉ ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯    "),  # folded ears, the white blaze, nose
       "cheer": ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ^ ▐▌ ^ ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯~   "),
       "sleep": ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ‒ ▐▌ ‒ ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯ z  "),
       "woof": ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ^ ▐▌ ^ ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─O─╯~   ")}  # mouth open; alternates with cheer
COL_FACES["left"] = ("  ◢▀▄▁▁▁▄▀◣ ", " ▐◉  ▐▌◉  ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯    ")  # eyes left / right: looking around
COL_FACES["right"] = ("  ◢▀▄▁▁▁▄▀◣ ", " ▐  ◉▐▌  ◉▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯    ")
COL_FACES["sniff"] = ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ^ ▐▌ ^ ▌ ", "  ▜▖ ▴  ▗▛  ", "   ╰─ω─╯    ")  # nose up; alternates with calm
COL_FACES["bone"] = ("  ◢▀▄▁▁▁▄▀◣ ", " ▐ ^ ▐▌ ^ ▌ ", "  ▜▖ ▼  ▗▛  ", "  o═╰ω╯═o   ")  # B: a bone across his mouth, happy eyes
COL_THANKS_FOR_A_BONE = ["a bone! for me? best. day. ever", "*crunch crunch* …thank you", "burying this one next to your oldest PR",
                         "you get me. you really get me", "mine. all mine. okay, back to work"]
MANY_BONES_IN_A_DAY = 5  # after this many he stops rotating his thank-yous and starts counting
COL_WORKING_CHATTER = ["lots of threads here. hold on", "where did I bury that PR…", "good dog. good report", "sniff sniff",
           "someone's been busy", "herding the facts into a pile", "almost. one more sniff", "squirrel!… no. focus",
           "digging. dirt everywhere", "oh! a bone! …no, a bug", "nose deep in the thread", "fetching. good boy"]
COL_IDLE_THOUGHTS = ["oh! look! a bone!", "squirrel!… no. back to work", "marking this branch as mine", "who's a good dev? you are",
         "is it walkies yet?", "I sniffed every PR. twice", "no treats until the tests pass", "that backlog smells funny",
         "zoomies after standup", "I'd fetch that PR for you, if I had thumbs", "sit. stay. review", "drop it. it's not your bug",
         "the mailman is a flaky test", "belly rubs accepted via Slack", "I buried a TODO around here somewhere",
         "tail wags per merged PR: many", "stop and sniff the diff", "not all who wander are lost. some are in Backlog"]
COL_SPEECH = {"mood": "cheer", "say": "", "until": 0}  # what Col in the corner is saying, and until when
CHAT_SIDE_PANE = {"comments": None, "top": 0, "topic": None}  # the chat's side pane: comments carried over from a preview, and its scroll
COL_LINES_BY_NEW_STATE_TYPE = {"started": ["in it goes. go get it", "moving. keep it moving", "off the leash. go"],
         "unstarted": ["queued up. one at a time", "back in the pen", "sit. stay. your turn comes"],
         "backlog": ["parked. no shame in that", "out of the way for now", "buried it in the backlog. I know where"],
         "triage": ["back to triage. decide later"],
         "completed": [], "canceled": ["gone. less to herd", "cancelled. good call if it was noise", "dropped it. good dog"]}
COL_REACTIONS = {"prio": ["priorities, priorities", "sorted. the urgent ones bark loudest", "the loud ones get fed first"],
         "copy": ["link's on your clipboard", "copied. go paste it", "fetched! dropped it on your clipboard"],
         "open": ["off to Linear. come back soon", "opening it over there", "you're leaving? I'll stare at the door"],
         "pr": ["to the PR. be kind in review", "chasing the PR. it's got a stick"],
         "comment": ["posted. words matter", "comment's up", "left my mark on the thread"], "ask": ["good question", "asking around", "nose to the ground"],
         "cancel_no": ["kept it. second thoughts are fine"], "team": ["sniffing around the team's pile", "everyone's issues. big pile", "new smells, new issues"],
         "project": ["project view. the long game"], "back": ["home. your own pile", "back to yours", "home. my blanket's here"],
         "refresh": ["fetching… sit… good", "fresh from Linear", "fetch! …good boy"], "inbox": ["reviews waiting. be the reviewer you want", "sit. stay. review"],
         "filter": ["narrowing it down", "sniff sniff", "on the scent", "oh! a bone! no, an issue"], "unfilter": ["back to the whole pile", "scent cleared"],
         "assign": ["handed it over. good herding", "new owner. I'll keep an eye on them"],
         "pick": ["there it is. go", "found it in the pile", "this one. fetch"],
         "nothing": ["nothing to undo. clean slate", "nothing to undo. nothing to dig up"]}
COL_PREVIEW_LINES = ["ooh, this one", "let's have a look", "good pick", "what's the story here", "reading glasses on", "this one smells promising",
         "*tilts head*", "ooh, a bone!"]
COL_TIPS = ["type a few letters to filter, no / needed", "3j jumps three rows down, like in vim",
        "space previews an issue; l inside shows its comments, j/k hop between them, C adds one", "a asks Col anything about the issue: Linear, Slack, GitHub, the web",
        "m lets you comment, or Col drafts it from a brief", "U undoes a status, priority, assignee or cancel",
        "R shows the PRs waiting on your review", "enter on an issue with PRs can start on the PR's branch",
        "? lists every key with what it does", "t and T browse a team or a project; h brings you back",
        "the age column goes red when something sits for 3 weeks",
        "▶ on a row means you're already working on it in herdr: w jumps to that workspace",
        "b is my brief: what you did the last two days, so you don't have to remember",
        "ctrl-n in a chat starts a fresh thread", "X cancels an issue, after asking",
        "G jumps to the bottom", "the chat with Claude is remembered per issue, in ~/.claude/work",
        "esc closes any window; one more esc closes the popup",
        "· quiet 3d means no commit on that branch for three days", "in a team view, chips show who's on what",
        "c opens Col with just the issue, no phases, no worktree", "the phase list remembers where you got to: ✓ ◐ ·",
        "first open of the day shows what moved overnight", "P sets priority; urgent ones sort to the top of their stage",
        "y copies the Linear link, L opens the app", "g opens the PR on GitHub; several PRs give you a list",
        "the popup reopens on the issue you last had selected", "status, priority and cancel apply at once; Linear catches up",
        "an issue with no repo still gets notes in ~/.claude/work/<ID>", "n creates an issue and Claude helps you write it properly",
        "the plan phase makes Col grill you before it touches code", "the review phase reads the diff with fresh eyes against the spec",
        "the team or project you used last is prefetched, so switching is instant", "PR badges come from Linear's GitHub link: conflict, draft, review, merged",
        "d and u in a preview scroll half a page", "in the chat, ↑↓ scroll and you can keep typing while Col thinks",
        "a comment drafted by Col is never posted until you say yes", "h in a team view goes home; h at home closes",
        "the count in the corner is how many you finished today", "edit Col's lines at the top of issues.py if you want a different voice", "Z sends Col off duty if you need the quiet",
        "B gives me a bone. just saying", "O walks you through the tour again"]
COL_CHEERS_FOR_DONE = ["nice one. {n} done today", "that's {n} today, you're on a roll", "✓ shipped. {n} down", "{n} done. keep going",
          "and that's {n}. the backlog fears you", "{n} done. tail going like mad", "{n} down. who's a good dev"]
# Mouse: what's on screen says what a click means. Every footer hint, list row and the esc in a dialog's corner
# registers a zone while it is drawn; Mouse hands a click back as the keys it stands for, the wheel as ↑/↓.
CLICK_ZONES, PENDING_KEYS = [], []  # (y, x0, x1, keys) drawn this frame, the newest on top · keys from a click, still to hand out
KEYCODE_BY_HINT_NAME = {"enter": 10, "empty-enter": 10, "any": 10, "esc": 27, "space": 32, "↑": curses.KEY_UP, "↓": curses.KEY_DOWN,
            "pgup": curses.KEY_PPAGE, "pgdn": curses.KEY_NPAGE}
# this ncurses (6.0, mouse v1) has no button 5: wheel down arrives as the "position" bit (probed)
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", curses.REPORT_MOUSE_POSITION)


def keycodes_named_in_hint(keys_text):
    """A hint's key text -> [(offset, length, keycode)], one per alternative that is a real key:
    "l/enter" -> l and enter, "ctrl-d/u" -> ^D and ^U, "type" (as in "type to filter") -> nothing."""
    is_ctrl_combination = keys_text.startswith("ctrl-")
    offset, keycodes = (5 if is_ctrl_combination else 0), []
    for alternative in [keys_text] if len(keys_text) == 1 else keys_text[offset:].split("/"):
        keycode = KEYCODE_BY_HINT_NAME.get(alternative) or (ord(alternative) if len(alternative) == 1 else None)
        if keycode:
            keycodes.append((offset, len(alternative), keycode & 31 if is_ctrl_combination else keycode))
        offset += len(alternative) + 1
    return keycodes


def register_clickable_row(y, x0, x1, steps, on_it=()):
    """A list row as a click target: `steps` rows from the cursor (negative: above) -> that many ↑/↓, then on_it;
    a click on the cursor's own row is just on_it."""
    CLICK_ZONES.append((y, x0, x1, [curses.KEY_DOWN if steps > 0 else curses.KEY_UP] * abs(steps) + list(on_it)))


def keys_for_click_at(y, x):
    return next((list(keys) for zy, x0, x1, keys in reversed(CLICK_ZONES) if zy == y and x0 <= x < x1), [])


class ScreenWithMouse:
    """The screen, with a mouse: clicks and the wheel come out of getch/get_wch as keys, so no key loop knows."""

    def __init__(self, screen):
        self.screen = screen
        curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON4_PRESSED | WHEEL_DOWN)
        curses.mouseinterval(0)  # no waiting to see if a press becomes a double click

    def __getattr__(self, name):
        return getattr(self.screen, name)

    def erase(self):
        CLICK_ZONES.clear()
        self.screen.erase()

    def _next_key(self, read):
        if not PENDING_KEYS:
            key = read()
            if key != curses.KEY_MOUSE:
                return key
            try:
                _, x, y, _, b = curses.getmouse()
            except curses.error:
                return -1
            PENDING_KEYS[:] = ([curses.KEY_UP] if b & curses.BUTTON4_PRESSED else [curses.KEY_DOWN] if b & WHEEL_DOWN
                          else keys_for_click_at(y, x))
        return PENDING_KEYS.pop(0) if PENDING_KEYS else -1

    def getch(self):
        key = self._next_key(self.screen.getch)
        note_key_for_guided_tour(key)
        return key

    def get_wch(self):
        key = self._next_key(self.screen.get_wch)
        note_key_for_guided_tour(key)
        if key == -1:
            raise curses.error("no input")
        return chr(key) if isinstance(key, int) and key < 256 else key


def col_says(text, mood="cheer", secs=10):
    """Col says text in his bubble for secs seconds, with that face."""
    COL_SPEECH.update(mood=mood, say=text, until=time.time() + secs)


def col_reacts_to(kind, mood="cheer"):
    """Col says one of his lines for something that just happened (see COL_REACTIONS); silent if he has none for it."""
    lines = COL_REACTIONS.get(kind) or []
    if lines:
        col_says(lines[int(time.time()) % len(lines)], mood)


def read_stored_value(name, default=None):
    try:
        return json.loads((CACHE_DIR / f"herdr-issues-{name}.json").read_text())
    except (OSError, ValueError):
        return default


def write_stored_value(name, value):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"herdr-issues-{name}.json").write_text(json.dumps(value))


def change_issue_optimistically(issue, input, fields_to_set_locally, label, undo=True):
    """Optimistic change: the row changes now, Linear gets it in the background, a failure lands in FAILED_CHANGE_MESSAGES.
    undo: remember how to put it back, for u."""
    if undo:
        input_that_puts_it_back = {"stateId": issue["state"]["id"]} if "state" in fields_to_set_locally else \
            {"assigneeId": (issue.get("assignee") or {}).get("id")} if "assignee" in fields_to_set_locally else {"priority": issue["priority"] or 0}
        UNDO_STACK.append((issue, input_that_puts_it_back, {field: issue[field] for field in fields_to_set_locally}, label))
    issue.update(fields_to_set_locally)

    def work():
        try:
            linear_graphql("mutation($id: String!, $in: IssueUpdateInput!) { issueUpdate(id: $id, input: $in) { success } }",
                {"id": issue["id"], "in": input})
        except Exception as error:
            FAILED_CHANGE_MESSAGES.append(f"{label} failed: {error}"[:160])
    threading.Thread(target=work, daemon=True).start()


LINEAR_KEYCHAIN_ITEM = "linear-api-key"  # the name the Linear API key is stored under in the macOS keychain


def linear_api_key_in_keychain():
    """The Linear API key from the macOS keychain, "" when there is none."""
    found = run_command("security", "find-generic-password", "-s", LINEAR_KEYCHAIN_ITEM, "-w")
    return "" if found.returncode else found.stdout.strip()


def save_linear_api_key_in_keychain(api_key):
    run_command("security", "add-generic-password", "-a", Path.home().name, "-s", LINEAR_KEYCHAIN_ITEM, "-w", api_key, "-U")


def linear_graphql(query, variables=None):
    api_key = linear_api_key_in_keychain()
    if not api_key:
        raise RuntimeError(f"no Linear key: security add-generic-password -a $USER -s {LINEAR_KEYCHAIN_ITEM} -w")
    request = urllib.request.Request("https://api.linear.app/graphql", json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 {"Content-Type": "application/json", "Authorization": api_key})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = json.load(response)
    except urllib.error.HTTPError as http_error:  # Linear puts the real reason in the body of a 400
        response_body = json.load(http_error)
    if response_body.get("errors"):
        raise RuntimeError(response_body["errors"][0]["message"])
    return response_body["data"]


def linear_graphql_with_spinner(screen, waiting_text, query, variables=None):
    """linear_graphql() off the main thread, with waiting_text and a spinner in the footer, so a slow network
    never freezes the popup."""
    return run_with_spinner(screen, waiting_text, lambda: linear_graphql(query, variables))


def fetch_issue_by_identifier(identifier):
    """One issue by its id (PLA-123), with the same fields as the list has. For issues that aren't in the list."""
    return linear_graphql("query($id: String!) { issue(id: $id) { %s } }" % ISSUE_FIELDS, {"id": identifier})["issue"]


def attachments_of(issue):
    """The issue's attachments (Linear puts linked GitHub PRs there); [] when it has none."""
    return (issue.get("attachments") or {}).get("nodes", [])


def workspaces_open_for_issue(issue):
    """The herdr workspaces whose name starts with the issue's id (that is the whole link between the two).
    Usually none or one; several when the issue was opened more than once."""
    prefix = issue["identifier"] + " "
    return [workspace for workspace in open_herdr_workspaces() if workspace["label"].startswith(prefix)]


def workspace_open_for_issue(issue):
    """The first herdr workspace open for the issue, or None: where a new tab for the issue goes."""
    return next(iter(workspaces_open_for_issue(issue)), None)


def describe_workspace(workspace):
    """One workspace as a row to pick from: its number, where its code is, what its agent is doing, how big it is."""
    repository = (workspace.get("worktree") or {}).get("repo_name") or "no repository"
    tabs = f"{workspace.get('tab_count', 1)} tab{'s' * (workspace.get('tab_count', 1) != 1)}"
    return f"ws {workspace['number']:<3} {repository[:24]:<24}  {workspace.get('agent_status') or '?':<8}  {tabs:<7}  {workspace['label'][:48]}"


def jump_to_a_workspace_of_issue(screen, issue):
    """Focus the issue's herdr workspace and close the popup; with several open, ask which one first.
    Returns a footer message when there is none, "" when the choice was cancelled."""
    workspaces = workspaces_open_for_issue(issue)
    if not workspaces:
        return f"no workspace open for {issue['identifier']} yet · enter starts one"
    picked = 0 if len(workspaces) == 1 else choose_from_list(
        screen, f"{issue['identifier']}  ·  {len(workspaces)} workspaces open, jump to which?", [describe_workspace(workspace) for workspace in workspaces],
        help=("This issue has more than one herdr workspace. Each row: its number in the sidebar, the repository it is in, "
              "what its agent is doing, and its name.",))
    if picked is None:
        return ""
    focus_workspace_and_close_popup(workspaces[picked])


def worktrees_on_branch(branch):
    """{repository name: folder} for every repository in the repositories folder that has branch checked out.
    Asks git, not herdr: herdr's worktree field goes missing for some workspaces. One git call per repository."""
    return {repository: folder for repository in local_repository_names()
            if (folder := worktree_path_for_branch(REPOSITORIES_FOLDER / repository, branch))}


BACKSPACE_KEYS = (curses.KEY_BACKSPACE, "\x7f", "\b", 127, 8)  # as get_wch gives them (str) and as getch does (int)


def text_after_typing(text, key):
    """text with key applied, when key edits text: a printable character is added, backspace removes the last one.
    None for any other key, so the caller can go on to its own keys. Takes what get_wch returns (str, any
    language) and what getch returns (int, ASCII only)."""
    if key in BACKSPACE_KEYS:
        return text[:-1]
    if isinstance(key, int) and 32 <= key < 127:
        key = chr(key)
    if isinstance(key, str) and key.isprintable():
        return text + key
    return None


def show_message_for_a_moment(screen, message):
    """An error in the footer of a screen that has no message line of its own: shown, then a 1.5s pause to read it."""
    show_status_message(screen, message)
    curses.napms(1500)


def cache_file_for_view(view):
    return MY_ISSUES_CACHE_FILE.with_name(f"herdr-issues-{view['kind']}-{view['id'][:8]}.json") if view else MY_ISSUES_CACHE_FILE


def fetch_issues(view=None):
    """My open issues, or everyone's in a team/project view ({"kind": "team"|"project", "id": …}). Saved to cache(view)."""
    if view:
        # ponytail: the 150 most recently updated; paginate if a team's or project's open pile gets bigger than that
        issues = linear_graphql("query($id: String!) { %s(id: $id) { issues(first: 150, orderBy: updatedAt, filter: {state: {type: "
                     "{in: %s}}}) { nodes { %s } } } }" % (view["kind"], json.dumps(OPEN_STATE_TYPES), ISSUE_FIELDS),
                     {"id": view["id"]})[view["kind"]]["issues"]["nodes"]
    else:
        issues = linear_graphql(MY_ISSUES_QUERY)["viewer"]["assignedIssues"]["nodes"]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file_for_view(view).write_text(json.dumps(issues))
    return issues


def choose_team(screen, title):
    """Team dialog, last used first. Returns (my user id, team dict with kind="team", or None)."""
    data = linear_graphql_with_spinner(screen, "loading teams…", "{ viewer { id } teams { nodes { id key name } } }")
    teams = {f"{team['key']:<6}{team['name']}": dict(team, kind="team") for team in data["teams"]["nodes"]}
    team_labels = most_recently_used_first("teams", sorted(teams))
    picked = choose_from_list(screen, title, team_labels)
    if picked is None:
        return data["viewer"]["id"], None
    remember_as_most_recently_used("teams", team_labels[picked], team_labels)
    return data["viewer"]["id"], teams[team_labels[picked]]


def choose_project(screen):
    """Open projects (not completed/canceled), last used first. Returns a project dict with kind="project", or None."""
    # first 100 and teams(first: 3): more than that and Linear rejects it as "Query too complex"
    open_projects = linear_graphql_with_spinner(
        screen, "loading projects…", '{ projects(first: 100, filter: {status: {type: {nin: ["completed", "canceled"]}}}) '
        "{ nodes { id name status { name } teams(first: 3) { nodes { key } } } } }")["projects"]["nodes"]
    projects = {f"{project['name'][:60]}  ·  {project['status']['name']} · {','.join(team['key'] for team in project['teams']['nodes'])}":
                dict(project, kind="project") for project in open_projects}
    project_labels = most_recently_used_first("projects", sorted(projects, key=str.lower))
    picked = choose_from_list(screen, "browse project", project_labels)
    if picked is None:
        return None
    remember_as_most_recently_used("projects", project_labels[picked], project_labels)
    return projects[project_labels[picked]]


def assign_to_me_if_unassigned(issue):
    """Assign an unassigned issue to me when I start on it. Best effort."""
    if not issue.get("assignee"):
        try:
            me = linear_graphql("{ viewer { id } }")["viewer"]["id"]
            linear_graphql("mutation($id: String!, $me: String!) { issueUpdate(id: $id, input: {assigneeId: $me}) { success } }",
                {"id": issue["id"], "me": me})
        except Exception:
            pass


def choose_new_status(screen, issue):
    """Menu of the team's statuses (current one pre-selected) -> move the issue. Returns a footer message."""
    order = ["triage", "backlog", "unstarted", "started", "completed", "canceled"]
    states = sorted(linear_graphql_with_spinner(
        screen, "loading statuses…", "query($t: String!) { team(id: $t) { states { nodes { id name type position } } } }",
        {"t": issue["team"]["id"]})["team"]["states"]["nodes"],
        key=lambda state: (order.index(state["type"]) if state["type"] in order else 9, state["position"]))
    state_names = [state["name"] for state in states]
    picked = choose_from_list(screen, f"{issue['identifier']}  ·  move to", state_names, state_names.index(issue["state"]["name"]))
    if picked is None or state_names[picked] == issue["state"]["name"]:
        return ""
    change_issue_optimistically(issue, {"stateId": states[picked]["id"]}, {"state": states[picked]}, f"{issue['identifier']} → {state_names[picked]}")
    return f"{issue['identifier']} → {state_names[picked]}  ·  U undo"


def confirm_and_cancel_issue(screen, issue):
    """Are-you-sure dialog -> the team's canceled state. Returns a footer message."""
    if choose_from_list(screen, f"cancel {issue['identifier']}?  {issue['title'][:24]}…", ["no, keep it", "yes, cancel it"]) != 1:
        return ""
    canceled_states = linear_graphql_with_spinner(
        screen, "looking up the team's canceled state…",
        'query($t: String!) { team(id: $t) { states(filter: {type: {eq: "canceled"}}) { nodes { id name type position } } } }',
        {"t": issue["team"]["id"]})["team"]["states"]["nodes"]
    if not canceled_states:
        return "team has no canceled state"
    canceled_state = min(canceled_states, key=lambda candidate_state: candidate_state["position"])
    change_issue_optimistically(issue, {"stateId": canceled_state["id"]}, {"state": canceled_state}, f"{issue['identifier']} canceled")
    return f"{issue['identifier']} canceled  ·  U undo"


def choose_new_priority(screen, issue):
    """Menu of priorities (current one pre-selected) -> update the issue. Returns a footer message."""
    levels = [(0, "▯▯▯ no priority")] + [(n, f"{bars} {word}") for n, (bars, word, _) in PRIORITY_DISPLAY.items()]
    level_labels = [label_text for _, label_text in levels]
    n = choose_from_list(screen, f"{issue['identifier']}  ·  priority", level_labels, [priority for priority, _ in levels].index(issue["priority"] or 0))
    if n is None or levels[n][0] == (issue["priority"] or 0):
        return ""
    label = f"{issue['identifier']} → {level_labels[n].split(' ', 1)[1]}"
    change_issue_optimistically(issue, {"priority": levels[n][0]}, {"priority": levels[n][0]}, label)
    return label + "  ·  U undo"


def choose_new_assignee(screen, issue):
    """Menu of the team's members (me first, current assignee marked) -> assign the issue. Returns a footer message."""
    data = linear_graphql_with_spinner(
        screen, "loading the team's members…",
        "query($team: String!) { viewer { id displayName } team(id: $team) { members { nodes { id displayName active } } } }",
        {"team": issue["team"]["id"]})
    my_user = data["viewer"]
    members_me_first = [my_user] + sorted((member for member in data["team"]["members"]["nodes"] if member["active"] and member["id"] != my_user["id"]),
                           key=lambda member: member["displayName"].lower())
    current_assignee_name = (issue.get("assignee") or {}).get("displayName")
    names = [f"{'●' if member['displayName'] == current_assignee_name else '○'}  {member['displayName']}{'  (me)' if member is my_user else ''}" for member in members_me_first]
    picked = choose_from_list(screen, f"{issue['identifier']}  ·  assign to", names + ["○  nobody"], 0, help=("Who should own it. ● is the current assignee.",))
    if picked is None:
        return ""
    new_assignee = members_me_first[picked] if picked < len(members_me_first) else None
    if (new_assignee or {}).get("displayName") == current_assignee_name or (new_assignee is None and current_assignee_name is None):
        return ""
    label = f"{issue['identifier']} → {new_assignee['displayName'] if new_assignee else 'unassigned'}"
    assignee_as_the_list_holds_it = {"id": new_assignee["id"], "displayName": new_assignee["displayName"]} if new_assignee else None
    change_issue_optimistically(issue, {"assigneeId": new_assignee["id"] if new_assignee else None},
                                {"assignee": assignee_as_the_list_holds_it}, label)
    return label + "  ·  U undo"


COL_SEND_OFF_LINES = {"issue": "go write it so anyone could pick it up", "plan": "think first, code later. go",
            "build": "fetch! bring back green tests", "review": "fresh eyes. be picky. sniff every line", "clean": "off you go, have a look around",
            "pr": "onto the PR's branch. make it mergeable"}


def show_col_send_off(screen, what):
    """Col cheers as you leave to work on an issue: a short beat before the popup closes."""
    if not UiState.col_is_visible:
        return
    if UiState.draw_screen_behind_dialog:
        UiState.draw_screen_behind_dialog()
    screen_height, screen_width = screen.getmaxyx()
    face = COL_FACES["cheer"]
    words = COL_SEND_OFF_LINES.get(what, "go get it")
    face_x = screen_width - len(face[2]) - 2
    for line_number, face_line in enumerate(face):
        screen.addnstr(screen_height - 5 + line_number, face_x, face_line, len(face_line), curses.color_pair(DEFAULT_TEXT) | (curses.A_BOLD if line_number in (1, 2) else 0))
    words = f"“{words}” ‹"
    screen.addnstr(screen_height - 4, max(face_x - len(words), 0), words, face_x, curses.color_pair(COL_ACCENT) | curses.A_BOLD)
    screen.refresh()
    curses.napms(700)


def run_with_spinner(screen, text, function_to_run):
    """Run fn in a thread with a spinner in the footer, so git/network don't freeze the popup. Re-raises fn's error."""
    outcome, spinner_frame = {}, 0

    def work():
        try:
            outcome["ok"] = function_to_run()
        except Exception as error:
            outcome["err"] = error
    threading.Thread(target=work, daemon=True).start()
    while not outcome:
        show_status_message(screen, f"{SPINNER_FRAMES[spinner_frame % len(SPINNER_FRAMES)]} {text}")
        spinner_frame += 1
        curses.napms(60)
        screen.timeout(0)
        screen.getch()
        screen.timeout(250)
    if "err" in outcome:
        raise outcome["err"]
    return outcome["ok"]


def move_issue_to_started(issue):
    """Triage/Backlog/Todo -> the team's first started state (In Progress). Best effort: shouldn't block starting work."""
    if issue["state"]["type"] == "started":
        return
    try:
        states = linear_graphql('query($t: String!) { team(id: $t) { states(filter: {type: {eq: "started"}}) { nodes { id position } } } }',
                     {"t": issue["team"]["id"]})["team"]["states"]["nodes"]
        if states:
            linear_graphql("mutation($id: String!, $s: String!) { issueUpdate(id: $id, input: {stateId: $s}) { success } }",
                {"id": issue["id"], "s": min(states, key=lambda s: s["position"])["id"]})
    except Exception:
        pass


ISSUE_IDENTIFIER_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")  # PLA-123, as Col writes them in the daily report


def linked_pull_requests(issue):
    """(url, title) of the GitHub PRs Linear has linked to the issue."""
    return [(attachment["url"], attachment["title"]) for attachment in attachments_of(issue) if "/pull/" in attachment["url"]]


def linked_pull_request_branches(issue):
    """[(repo, branch, PR number)] for linked PRs whose repo is in the repositories folder, from Linear's GitHub metadata."""
    branches = []
    for attachment in attachments_of(issue):
        github_metadata = attachment.get("metadata") or {}
        repository = github_metadata.get("repoName") or attachment["url"].split("/")[-3]
        is_pull_request_with_a_branch = "/pull/" in attachment["url"] and github_metadata.get("branch")
        is_already_listed = (repository, github_metadata.get("branch")) in [already_listed[:2] for already_listed in branches]
        if is_pull_request_with_a_branch and (REPOSITORIES_FOLDER / repository / ".git").is_dir() and not is_already_listed:
            branches.append((repository, github_metadata["branch"], github_metadata.get("number")))
    return branches


def pull_request_badge_key(meta):
    """PULL_REQUEST_BADGES key for one PR, from the GitHub metadata Linear keeps on the attachment."""
    status = (meta or {}).get("status")
    if status in ("open", "inReview"):
        return "conflict" if meta.get("hasConflicts") else "draft" if meta.get("draft") else \
            "review" if status == "inReview" else "open"
    return status if status in PULL_REQUEST_BADGES else "linked"


def pull_request_badge(issue):
    """(text, colour) for the row: the linked PR most in need of attention, ×N when there are several. ("", 0) if none."""
    badge_keys = [pull_request_badge_key(attachment.get("metadata")) for attachment in attachments_of(issue) if "/pull/" in attachment["url"]]
    if not badge_keys:
        return "", 0
    text, colour = PULL_REQUEST_BADGES[min(badge_keys, key=list(PULL_REQUEST_BADGES).index)]
    return text + (f" ×{len(badge_keys)}" if len(badge_keys) > 1 else ""), colour


def colour_for_issue_age(updated_at, now):
    """Age heat: white today, grey this week, dim after, red when it's been sitting for three weeks."""
    days = (now - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).days
    return DEFAULT_TEXT if days < 1 else MUTED if days < 7 else DIM if days < 21 else RED


def initials_and_chip_colour(name):
    """("ES", colour) for an assignee chip: initials, colour hashed from the name so a person keeps theirs."""
    parts = [p for p in re.split(r"[.\s_-]+", name or "") if p]
    return ("".join(p[0] for p in parts)[:2].upper() or "?"), ASSIGNEE_CHIP_COLOURS[sum(map(ord, name or "")) % len(ASSIGNEE_CHIP_COLOURS)]


def snapshot_of_states_and_badges(issues):
    """{identifier: (state, PR badge)}: what the digest compares against."""
    return {i["identifier"]: (i["state"]["name"], pull_request_badge(i)[0]) for i in issues}


def changes_since_snapshot(snapshot_before, issues):
    """[(identifier, title, what changed)] since before (see snapshot), for the first-open-of-the-day digest."""
    changes = []
    for issue in issues:
        state_and_badge_before, badge = snapshot_before.get(issue["identifier"]), pull_request_badge(issue)[0]
        if state_and_badge_before is None:
            changes.append((issue["identifier"], issue["title"], "new"))
        elif state_and_badge_before[0] != issue["state"]["name"]:
            changes.append((issue["identifier"], issue["title"], f"{state_and_badge_before[0]} → {issue['state']['name']}"))
        elif state_and_badge_before[1] != badge and badge:
            changes.append((issue["identifier"], issue["title"], f"PR {badge}"))
    return changes


def show_key_reference(screen):
    """? : every key, grouped and explained, in two columns. Any key closes it."""
    columns = [[], []]
    for group_number, (group, keys_of_group) in enumerate(KEY_REFERENCE_GROUPS):
        column = columns[0] if group_number < 3 else columns[1]
        column += [("", group)] + keys_of_group + [("", "")]
    screen_height, screen_width = screen.getmaxyx()
    column_width = min(44, (screen_width - 10) // 2)
    box_height = min(max(map(len, columns)) + 3, screen_height - 10)
    line, box_width = draw_dialog_box(screen, "keys  ·  every key", box_height, 2 * column_width + 6)
    for column_number, rows_of_column in enumerate(columns):
        column_x = 3 + column_number * (column_width + 2)
        for row_in_box, (keys_text, description) in enumerate(rows_of_column[:box_height - 3]):
            if keys_text:
                line(2 + row_in_box, keys_text, curses.color_pair(ACCENT) | curses.A_BOLD, column_x + 1)
                line(2 + row_in_box, description[:column_width - 20], 0, column_x + 19)
            elif description:
                line(2 + row_in_box, description.upper(), curses.color_pair(MUTED) | curses.A_BOLD, column_x)
    draw_footer_from_text(screen, "any key closes · K hides or shows these footer actions")
    screen.refresh()
    screen.timeout(-1)
    screen.getch()
    screen.timeout(250)


# The tour for someone new. A welcome card, then a walk through the real app: Col says each step in his bubble
# (it stays in view on every screen) and the step is done when its key is pressed, for real: the preview really
# opens, The Daily Bark really opens, esc really goes back. It ends on a card about the green markers.
# ponytail: a step only looks at which key was pressed, not where; someone who wanders off mid-tour can tick a step
# from another screen. Give steps a "which screen" check if that turns out to confuse people.
ENTER_KEYCODES = (10, 13, curses.KEY_ENTER)
FORWARD_KEYCODES, BACK_KEYCODES = (ord("l"),) + ENTER_KEYCODES, (27, ord("h"))
GUIDED_TOUR_STEPS = [  # (what Col says, the keys that do it). Kept short: the bubble has about 75 characters on a small popup
    ("these are your issues, the furthest along on top. press j to move down", (ord("j"), curses.KEY_DOWN)),
    ("and k to move back up", (ord("k"), curses.KEY_UP)),
    ("space previews the issue under the cursor", (ord(" "),)),
    ("esc or h goes back. that works everywhere. try it", BACK_KEYCODES),
    ("b opens The Daily Bark: my recap of your last two days", (ord("b"),)),
    ("j / k pick a topic. l or enter shows what you can do with it", FORWARD_KEYCODES),
    ("go to its issue, start it, or ask me. esc or h backs out", BACK_KEYCODES),
    ("esc or h once more, and you're back at your issues", BACK_KEYCODES),
    ("l or enter starts work on an issue. press it and have a look", FORWARD_KEYCODES),
    ("this is where work begins. esc or h backs out for now", BACK_KEYCODES),
]
TOUR_WELCOME_CARD = ("welcome", [
    "This is **Collinear**: your Linear issues one key away, and everything you have in progress.",
    "",
    "I'll walk you through it for real. **Do what my speech bubble says**, down in the corner, and watch what happens.",
    "",
    "Two pairs of keys work everywhere:  `l` / `enter` goes forward or picks,  `esc` / `h` goes back.  `q` closes "
    "the popup."])
TOUR_CLOSING_CARD = ("the green markers", [
    "@green ▶ ws 3 · my-repo",
    "",
    "A green marker on a row means **a herdr workspace is open for that issue**: work in progress, right now, on this "
    "machine. It names the workspace (the number in herdr's sidebar) and the repository.",
    "",
    "So the green rows answer *what am I in the middle of?*  Put the cursor on one and press `w` to jump straight "
    "into it: the fastest way to move between the things you have going.",
    "",
    "The loop:  **open, look for green,** `w`.   `l` / `enter` to start something new.   `b` in the morning.   "
    "`O` for this tour again."])


def show_tour_card(screen, card, footer_text):
    """One card of the tour over the list; returns the key that was pressed on it. A line is Markdown, or
    "@green …" to show the green marker as it looks on a row."""
    title, text_lines = card
    attribute_by_style = daily_report_text_attributes()
    line, box_width = draw_dialog_box(screen, "Col  ·  a quick tour", 19, 80)
    line(3, title, curses.color_pair(ACCENT) | curses.A_BOLD, 4)
    row = 5
    for text_line in text_lines:
        if text_line.startswith("@green "):
            line(row, f" {text_line[len('@green '):]} ", curses.color_pair(GREEN) | curses.A_REVERSE | curses.A_BOLD, 5)
            row += 1
            continue
        for segments in markdown_to_styled_lines(text_line, box_width - 9) or [[]]:  # "" is a blank row
            column = 4
            for text, style in segments:
                line(row, text, attribute_by_style[style], column)
                column += len(text)
            row += 1
    draw_footer_from_text(screen, footer_text)
    screen.refresh()
    screen.timeout(-1)
    key = screen.getch()
    screen.timeout(250)
    return key


def start_guided_tour(screen):
    """The welcome card; l/enter starts the walk-through (Col takes it from there, in his bubble), esc/h/q declines."""
    col_says("a tour? I love tours", "cheer", 30)
    if show_tour_card(screen, TOUR_WELCOME_CARD, "l/enter start the tour · esc/h not now") in FORWARD_KEYCODES:
        UiState.guided_tour_step = 0
        UiState.col_is_visible = True  # he is the guide


def note_key_for_guided_tour(key):
    """Every key pressed anywhere passes through here: the tour's current step is done when its key is pressed."""
    step = UiState.guided_tour_step
    if step is None:
        return
    keycode = ord(key) if isinstance(key, str) and len(key) == 1 else key
    if keycode in GUIDED_TOUR_STEPS[step][1]:
        UiState.guided_tour_step = step + 1 if step + 1 < len(GUIDED_TOUR_STEPS) else None
        UiState.tour_closing_card_is_due = UiState.guided_tour_step is None
    elif keycode == ord("q"):  # closing the popup ends the tour
        UiState.guided_tour_step = None


def words_of_guided_tour_step():
    step = UiState.guided_tour_step
    return f"{step + 1}/{len(GUIDED_TOUR_STEPS)}  {GUIDED_TOUR_STEPS[step][0]}"


def show_review_inbox(screen):
    """R: open PRs where your review is requested (GitHub, via prs.py). enter opens one in the browser."""
    import prs
    pull_requests = run_with_spinner(screen, "checking PRs waiting on your review…", prs.fetch)["review"]["nodes"]
    if not pull_requests:
        return "nothing waiting on your review"
    pull_request_labels = [f"{pull_request['repository']['nameWithOwner'].split('/')[-1][:20]:<20}  #{pull_request['number']:<5} {pull_request['title'][:48]:<48}  {prs.state(pull_request)[1]}"
             for pull_request in pull_requests]
    picked = choose_from_list(screen, f"review inbox  ·  {len(pull_requests)} waiting on you", pull_request_labels,
               help=("Open PRs where your review was requested, from GitHub. enter opens one in the browser.",))
    if picked is None:
        return ""
    subprocess.run(["open", pull_requests[picked]["url"]])
    return f"opened #{pull_requests[picked]['number']}"


def show_morning_digest(screen, changes):
    """First open of the day: what moved since you last looked, 3s or any key."""
    change_lines = [f"{identifier:<9} {title[:50]:<50}  {what_changed}" for identifier, title, what_changed in changes[:12]]
    if len(changes) > 12:
        change_lines.append(f"… and {len(changes) - 12} more")
    line, box_width = draw_dialog_box(screen, "good morning  ·  since you last looked", len(change_lines) + 5, max(len(line_text) for line_text in change_lines) + 8)
    for row_in_box, (text, (_, _, what_changed)) in enumerate(zip(change_lines, changes)):
        line(2 + row_in_box, text, curses.color_pair(GREEN if "merged" in what_changed or "Done" in what_changed else 0), 3)
    line(len(change_lines) + 3, "any key", curses.color_pair(DIM), 3)
    screen.refresh()
    screen.timeout(3000)
    screen.getch()
    screen.timeout(250)


def build_issue_rows(issues):
    """("head", (state name, state type)) / ("issue", issue) rows: started -> todo -> backlog -> triage, furthest
    along on top, urgent first within a state."""
    sort_order = lambda issue_to_sort: (OPEN_STATE_TYPES.index(issue_to_sort["state"]["type"]), -issue_to_sort["state"]["position"], issue_to_sort["priority"] or 9, issue_to_sort["identifier"])
    STATE_POSITION_BY_NAME.update((issue_to_sort["state"]["name"], issue_to_sort["state"]["position"]) for issue_to_sort in issues)
    STARTED_STATE_POSITIONS[:] = sorted({issue_to_sort["state"]["position"] for issue_to_sort in issues if issue_to_sort["state"]["type"] == "started"})
    rows, previous_header = [], None
    for issue in sorted((issue_to_sort for issue_to_sort in issues if issue_to_sort["state"]["type"] in OPEN_STATE_TYPES), key=sort_order):
        header = (issue["state"]["name"], issue["state"]["type"])
        if header != previous_header:
            previous_header = header
            rows.append(("head", header))
        rows.append(("issue", issue))
    return rows or [("note", "nothing open assigned to you · n for a new issue")]


def rows_matching_filter(rows, query):
    """Rows matching query on id/title, headers without issues dropped, a blank line before each later header."""
    filter_lowercase = query.lower()
    rows_kept = [row for row in rows if row[0] != "issue" or filter_lowercase in f"{row[1]['identifier']} {row[1]['title']}".lower()]
    rows_shown = []
    for row_number, (kind, value) in enumerate(rows_kept):
        if kind == "head":
            if row_number + 1 == len(rows_kept) or rows_kept[row_number + 1][0] != "issue":
                continue
            rows_shown += [("blank", None)] * bool(rows_shown)
        rows_shown.append((kind, value))
    return rows_shown


def most_recently_used_first(kind, items):
    """items with the most recently used (see remember) first."""
    try:
        used = json.loads((CACHE_DIR / f"herdr-issues-{kind}.json").read_text())
    except (OSError, ValueError):
        used = []
    return [item for item in used if item in items] + [item for item in items if item not in used]


def remember_as_most_recently_used(kind, item, items):
    (CACHE_DIR / f"herdr-issues-{kind}.json").write_text(json.dumps([item] + [other_item for other_item in most_recently_used_first(kind, items) if other_item != item]))


def local_repository_names():
    """The git repositories in REPOSITORIES_FOLDER (~/Development unless local_settings.json says otherwise), most
    recently used first. [] when that folder doesn't exist: setup says how to point it at the right one."""
    if not REPOSITORIES_FOLDER.is_dir():
        return []
    return most_recently_used_first("repos", sorted(p.name for p in REPOSITORIES_FOLDER.iterdir() if (p / ".git").is_dir()))


def ensure_worktree_for_branch(repo, branch):
    """Folder with branch checked out, making <repo>-wt/<branch> (same layout as prs.py) if needed."""
    run_command("git", "-C", str(repo), "fetch", "origin")  # ponytail: offline is fine, worktree add falls back to local refs
    if path := worktree_path_for_branch(repo, branch):
        return path
    path = f"{repo}-wt/{branch.replace('/', '-')}"
    # existing local/remote branch first, else a new branch off the default branch
    if run_command("git", "-C", str(repo), "worktree", "add", path, branch).returncode:
        out = run_command("git", "-C", str(repo), "worktree", "add", "-b", branch, path, "origin/HEAD")
        if out.returncode:
            raise RuntimeError(f"worktree failed: {out.stderr.strip()[-120:]}")
    return path


def render_phase_prompt(phase, issue, paths, notes):
    # clean slate is deliberately without common.md: no worktrees, phases or notes conventions
    text = ("" if phase == "clean" else (PROMPTS_DIR / "common.md").read_text() + "\n") + (PROMPTS_DIR / f"{phase}.md").read_text()
    for placeholder, replacement in {"id": issue["identifier"], "title": issue["title"], "url": issue["url"], "branch": issue["branchName"],
                 "worktrees": "\n".join(f"- {folder}" for folder in paths) or "- none: this issue isn't tied to a repo, you start in the notes folder",
                 "notes": str(notes)}.items():
        text = text.replace("{" + placeholder + "}", replacement)
    return text


def set_up_colour_pairs():
    curses.use_default_colors()
    # Linear-ish: indigo accent, grey chrome; selection is a neovim-style cursorline (dark bg + accent bar), not inverse
    c = curses
    acc, mut, dim, sel_fg, sel_bg, red, border, code, green, yellow, orange, purple = \
        (105, 245, 240, 231, 239, 203, 238, 152, 114, 221, 209, 141) if c.COLORS >= 256 else \
        (c.COLOR_BLUE, c.COLOR_WHITE, c.COLOR_WHITE, c.COLOR_WHITE, c.COLOR_BLACK, c.COLOR_RED, c.COLOR_WHITE,
         c.COLOR_CYAN, c.COLOR_GREEN, c.COLOR_YELLOW, c.COLOR_RED, c.COLOR_MAGENTA)
    sky = 75 if c.COLORS >= 256 else c.COLOR_CYAN
    for n, fg, bg in ((ACCENT, acc, -1), (MUTED, mut, -1), (SELECTED_ROW, sel_fg, sel_bg), (RED, red, -1), (BORDER, border, -1),
                      (CODE, code, -1), (GREEN, green, -1), (YELLOW, yellow, -1), (ORANGE, orange, -1),
                      (PURPLE, purple, -1), (SELECTED_ROW_MARKER, acc, sel_bg), (TITLE_TAG, sel_bg, acc), (DIM, dim, -1), (DEFAULT_TEXT, -1, -1),
                      (DONE_FLASH, sel_bg, green), (COL_FACE, sel_fg, -1), (COL_ACCENT, sky, -1), (COL_BADGE, sel_fg, sky),
                      (COL_BADGE_QUIET_TEXT, sel_bg, sky)):  # Col: white face, sky blue frame; COL_BADGE: white on sky; quiet text: dark grey on sky
        curses.init_pair(n, fg, bg)


COL_CORNER_WIDTH = 17  # Col's outline and name tag, bottom right: the footer stops short of it


def draw_footer_from_text(screen, text):
    """The footer, on every screen: everything you can do right here, as "key what" parts separated by " · ",
    packed onto up to two rows above Col's row. K hides it (? stays)."""
    pairs = [tuple(part.strip().split(" ", 1)) if " " in part.strip() else (part.strip(), "") for part in text.split(" · ")]
    draw_footer_hints(screen, None, 2, pairs)


def draw_footer_hints(screen, fixed_row, first_column, hints):
    """Footer like opencode's: bright key, muted description. Packs the hints onto rows ending at the bottom row
    (fixed_row=None: as many rows as needed, at most two), stopping short of Col's corner. When they need more than
    two rows the first ones win, since they matter most, and ? (which lists every key) is kept at the end.
    With the hints off (K), only ? survives."""
    screen_height, usable_width = screen.getmaxyx()
    if not UiState.footer_hints_visible:
        hints = [(keys_text, description) for keys_text, description in hints if keys_text == "?"] or [("?", "all keys")]
    if UiState.col_is_visible:
        usable_width -= COL_CORNER_WIDTH
    hint_rows, hints_in_row, used_width = [], [], first_column
    for keys_text, description in hints:  # pack
        hint_width = len(keys_text) + len(description) + 4
        if hints_in_row and used_width + hint_width > usable_width - 1:
            hint_rows.append(hints_in_row)
            hints_in_row, used_width = [], first_column
        hints_in_row.append((keys_text, description))
        used_width += hint_width
    hint_rows.append(hints_in_row)
    if fixed_row is not None:
        hint_rows = hint_rows[:1]
    elif len(hint_rows) > 2:
        hint_for_all_keys = next((hint for cut_row in hint_rows[2:] for hint in cut_row if hint[0] == "?"), None)
        hint_rows = hint_rows[:2]
        if hint_for_all_keys:
            width_of_hint = lambda hint: len(hint[0]) + len(hint[1]) + 4
            while hint_rows[1] and first_column + sum(map(width_of_hint, hint_rows[1] + [hint_for_all_keys])) > usable_width - 1:
                hint_rows[1].pop()
            hint_rows[1].append(hint_for_all_keys)
    first_row = screen_height - len(hint_rows) if fixed_row is None else fixed_row
    if fixed_row is None:  # both footer rows are ours: clear what the screen behind left there
        for footer_row in (screen_height - 2, screen_height - 1):
            screen.addnstr(footer_row, 0, " " * max(usable_width - 1, 0), max(usable_width - 1, 0))
    for row_number, hints_in_row in enumerate(hint_rows):
        hint_x = first_column
        for keys_text, description in hints_in_row:
            if (keycodes := keycodes_named_in_hint(keys_text)) and hint_x < usable_width - 1:  # click the hint: its first key; click one of "j/k": that one
                CLICK_ZONES.append((first_row + row_number, hint_x, min(hint_x + len(keys_text) + len(description) + 1, usable_width - 1), [keycodes[0][2]]))
                CLICK_ZONES.extend((first_row + row_number, hint_x + offset_in_keys, hint_x + offset_in_keys + length_in_keys, [keycode]) for offset_in_keys, length_in_keys, keycode in keycodes)
            for text, attribute in ((keys_text, curses.color_pair(ACCENT) | curses.A_BOLD), (f" {description}   ", curses.color_pair(MUTED))):
                if hint_x < usable_width - 1:
                    screen.addnstr(first_row + row_number, hint_x, text, usable_width - 1 - hint_x, attribute)
                hint_x += len(text)


def show_status_message(screen, text):
    screen_height, screen_width = screen.getmaxyx()
    screen.move(screen_height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(screen_height - 1, 2, f"⟳ {text}", screen_width - 3, curses.color_pair(ACCENT) | curses.A_BOLD)
    screen.refresh()


def draw_dialog_box(screen, title, box_height, box_width, top_left_corner=None):
    """Draw a centred rounded box over the list with a headline tag; returns line(y, text, attr, x) relative to it.
    title "where  ·  what": "where" becomes the accent tag, "what" sits next to it. The first draw of a new title
    pops the box open (3 frames, ~40ms)."""
    if UiState.draw_screen_behind_dialog:
        UiState.draw_screen_behind_dialog()
    screen_height, screen_width = screen.getmaxyx()
    box_width, box_height = min(box_width, screen_width - 4), min(box_height, screen_height - 10)  # Col's corner (the bottom 6 rows) stays clear, so he's seen on every screen
    box_y, box_x = max(min((screen_height - box_height) // 2, screen_height - 7 - box_height), 0), max((screen_width - box_width) // 2, 0)
    if top_left_corner:  # a dropdown: the box goes where it's told, kept on screen
        box_y, box_x = max(min(top_left_corner[0], screen_height - box_height), 0), max(min(top_left_corner[1], screen_width - box_width - 1), 0)
    border_colour = curses.color_pair(BORDER)

    def box(box_y, box_x, box_height, box_width):
        screen.addnstr(box_y, box_x, "╭" + "─" * (box_width - 2) + "╮", box_width, border_colour)
        for y in range(1, box_height - 1):
            screen.addnstr(box_y + y, box_x, "│" + " " * (box_width - 2) + "│", box_width, border_colour)
        screen.addnstr(box_y + box_height - 1, box_x, "╰" + "─" * (box_width - 2) + "╯", box_width, border_colour)
    if UiState.title_of_open_dialog != title:
        UiState.title_of_open_dialog = title
        for fraction_of_full_size in (0.35, 0.6, 0.85):
            growing_height, growing_width = max(int(box_height * fraction_of_full_size), 3), max(int(box_width * fraction_of_full_size), 8)
            box(box_y + (box_height - growing_height) // 2, box_x + (box_width - growing_width) // 2, growing_height, growing_width)
            screen.refresh()
            curses.napms(14)
            UiState.draw_screen_behind_dialog() if UiState.draw_screen_behind_dialog else screen.erase()
    box(box_y, box_x, box_height, box_width)
    CLICK_ZONES[:] = [(box_y + 1, box_x + box_width - 7, box_x + box_width - 2, [27])]  # nothing behind a dialog is clickable; its "esc" corner is
    line = lambda y, text, attr=0, x=0: screen.addnstr(box_y + y, box_x + x, text, max(box_width - x, 0), attr)
    line.first_row_on_screen, line.first_column_on_screen = box_y, box_x  # for register_clickable_row and dropdowns: where the box starts on the screen
    tag_text, _, headline = title.partition("  ·  ")
    line(1, f" {tag_text} ", curses.color_pair(TITLE_TAG) | curses.A_BOLD, 2)
    line(1, headline, curses.color_pair(ACCENT) | curses.A_BOLD, len(tag_text) + 5)
    line(1, "esc", curses.color_pair(DIM), box_width - 6)
    return line, box_width


def ask_for_one_line(screen, title, hint="enter create · esc cancel", examples=(), text=""):
    """Centred one-line text input. enter returns the text, esc returns None.
    examples: lines shown under the input while it is empty (what to type). text: what it starts with."""
    while True:
        example_lines = [wrapped_line for example in examples for wrapped_line in (textwrap.wrap(example, 72 - 12, subsequent_indent="  ") or [""])]
        line, box_width = draw_dialog_box(screen, title, 6 + len(example_lines), 72)
        line(2, "›", curses.color_pair(ACCENT) | curses.A_BOLD, 3)
        line(2, f"{text}▏"[-(box_width - 8):], curses.A_BOLD, 5)
        for line_number, example in enumerate(example_lines):
            line(3 + line_number, example, curses.color_pair(DIM if text else MUTED), 5)
        draw_footer_from_text(screen, hint)
        screen.refresh()
        try:
            key = screen.get_wch()  # wide chars, so å/ä/ö work in titles
        except curses.error:  # the 250ms timeout main() sets
            continue
        if key == "\x1b":
            return None
        if key in ("\n", "\r", curses.KEY_ENTER):
            return text.strip() or None
        text = text if (typed := text_after_typing(text, key)) is None else typed


SETUP_PASSED_FILE = CACHE_DIR / "herdr-issues-setup.json"  # written once every check passes; delete it to re-run
DEMO_SETUP_MODE = "--demo-setup" in sys.argv  # python3 issues.py --demo-setup: the onboarding screen with a fake missing step


def saved_user_name():
    """The name Col calls you, from the cache (set in setup). "" until set."""
    return ((read_stored_value("name") or {}).get("name") or "").strip()


def saved_first_name():
    return saved_user_name().split(" ")[0] if saved_user_name() else ""


def greeting_for_this_hour():
    hour = datetime.now().hour
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def ask_for_user_name(screen):
    """Ask what Col should call you. Returns a footer message."""
    name = ask_for_one_line(screen, "setup  ·  your name", "enter save · esc back",
               ("what Col should call you. first name is fine, it's for greetings and your daily report",
                f"now: {saved_user_name()}" if saved_user_name() else "kept in the cache folder, nowhere else"))
    if not name:
        return ""
    write_stored_value("name", {"name": name})
    return f"hi {name.split(' ')[0]}. good to meet you"


HERDR_VERSION_TESTED_WITH = (0, 9, 5)


def herdr_version_from(version_output):
    """(0, 9, 5) from "herdr 0.9.5"; None if it doesn't look like a version."""
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version_output)
    return tuple(map(int, found.groups())) if found else None


def shortened_folder(folder, room):
    """A folder for a narrow column: ~ for the home folder, and when it is still too long, its end after an ellipsis."""
    text = str(folder).replace(str(Path.home()), "~", 1)
    return text if len(text) <= room else "…" + text[-(room - 1):]


def set_repositories_folder(folder_text):
    """Point both popups at another folder of git repositories, now and from the next start on (local_settings.json).
    Returns a footer message; nothing changes if the folder doesn't exist."""
    global REPOSITORIES_FOLDER
    folder = Path(folder_text).expanduser()
    if not folder.is_dir():
        return f"no such folder: {folder_text}"
    import prs
    prs.save_local_setting("repositories_folder", str(folder).replace(str(Path.home()), "~", 1))
    REPOSITORIES_FOLDER = prs.REPOSITORIES_FOLDER = folder
    count = len(local_repository_names())
    return f"repositories folder saved: {count} git repositor{'y' if count == 1 else 'ies'} in it"


def ask_for_repositories_folder(screen):
    """Ask which folder the git repositories are cloned into. Returns a footer message."""
    shown_folder = str(REPOSITORIES_FOLDER).replace(str(Path.home()), "~", 1)
    folder_text = ask_for_one_line(screen, "setup  ·  repositories folder", "enter save · esc back",
                                   ("the folder your git repositories are cloned into, for example ~/code or ~/src",
                                    "an issue's worktrees are made next to its repository in there"), text=shown_folder)
    return set_repositories_folder(folder_text) if folder_text else ""


def quick_setup_checks():
    """[(name, ok, how)] for what the popup needs, the quick ones (<1s). how: what the dev does, or "paste" if
    run_setup() can take the secret itself."""
    return [("your name for Col", bool(saved_user_name()), "paste"),
            ("Linear API key in the keychain", bool(linear_api_key_in_keychain()), "paste"),
            ("gh signed in to GitHub", not run_command("gh", "auth", "status").returncode, "paste"),
            ("claude on the PATH", not run_command("which", "claude").returncode, "npm i -g @anthropic-ai/claude-code; run claude once to sign in"),
            (f"your repositories in {shortened_folder(REPOSITORIES_FOLDER, 18)}", REPOSITORIES_FOLDER.is_dir(), "paste"),
            herdr_version_check()]


def herdr_version_check():
    """The setup line for herdr itself: fine from the version this was tested with."""
    version = herdr_version_from(run_command("herdr", "--version").stdout)
    tested_with = ".".join(map(str, HERDR_VERSION_TESTED_WITH))
    if version is None:
        return ("herdr on the PATH", False, "herdr isn't answering: is it installed?")
    return (f"herdr {'.'.join(map(str, version))}", version >= HERDR_VERSION_TESTED_WITH, f"tested with {tested_with} and newer: run  herdr update")


SERVICES_COL_LOOKS_THINGS_UP_IN = ("Linear", "Slack")
DEFAULT_CONNECTOR_NAMES = {service: f"claude.ai {service}" for service in SERVICES_COL_LOOKS_THINGS_UP_IN}


def connectors_listed_by_claude(listing):
    """[(server name, is connected)] from the output of `claude mcp list`: lines like "claude.ai Linear: https://… - ✔ Connected"."""
    servers = []
    for line in listing.splitlines():
        name_and_address, separator, state = line.rpartition(" - ")
        if separator and ": " in name_and_address:
            servers.append((name_and_address.partition(": ")[0].strip(), "Connected" in state))
    return servers


def connector_for_service(service, servers):
    """(server name, is connected) of the MCP server for Linear or Slack, whatever it is called on this machine:
    any server with the service's name in its own. A connected one wins, then a claude.ai one. (None, False) if none."""
    candidates = [(name, connected) for name, connected in servers if re.search(rf"\b{service}\b", name, re.I)]
    candidates.sort(key=lambda server: (not server[1], not server[0].startswith("claude.ai")))
    return candidates[0] if candidates else (None, False)


def connector_names():
    """{"Linear": server name, "Slack": server name} as setup last found them; the claude.ai names until it has looked."""
    return {**DEFAULT_CONNECTOR_NAMES, **(read_stored_value("connectors") or {})}


def mcp_tool_prefix(server_name):
    """How Claude Code names a server's tools: "claude.ai Linear" -> "mcp__claude_ai_Linear__"."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", server_name) + "__"


def connector_setup_checks():
    """The Linear and Slack connectors Col's answers need, same shape as quick_setup_checks(). Slow (~10s: claude
    pings every server). Remembers what each one is called here, so Col asks the right server."""
    if DEMO_SETUP_MODE:  # --demo-setup: pretend the Slack connector is missing, to see the onboarding screen
        return [("Linear connector in Claude Code", True, ""), ("Slack connector in Claude Code", False, "(demo) claude.ai → settings → connectors → Slack; then /mcp in claude")]
    servers = connectors_listed_by_claude(run_command("claude", "mcp", "list").stdout) if not run_command("which", "claude").returncode else []
    checks, names_found = [], {}
    for service in SERVICES_COL_LOOKS_THINGS_UP_IN:
        server_name, is_connected = connector_for_service(service, servers)
        if server_name:
            names_found[service] = server_name
        how_to_fix = f'"{server_name}" needs signing in: /mcp in claude' if server_name else \
            f"claude.ai → settings → connectors → {service}; then /mcp in claude"
        checks.append((f"{service} connector in Claude Code", is_connected, how_to_fix))
    if names_found:
        write_stored_value("connectors", names_found)
    return checks


def ask_for_linear_api_key(screen):
    """Ask for a Linear API key, keep it in the keychain if Linear accepts it. Returns a footer message."""
    token = ask_for_one_line(screen, "setup  ·  Linear API key", "enter save · esc back",
                ("linear.app → settings → security & access → personal API keys → new key",
                 "it is stored in the macOS keychain as linear-api-key, nowhere else. replaces the old one."))
    if not token:
        return ""
    key_before = linear_api_key_in_keychain()
    save_linear_api_key_in_keychain(token)
    try:
        linear_graphql("{ viewer { id } }")
    except Exception as e:
        if key_before:  # put the working one back
            save_linear_api_key_in_keychain(key_before)
        else:
            run_command("security", "delete-generic-password", "-s", LINEAR_KEYCHAIN_ITEM)
        return f"Linear said no, key not changed: {e}"[:120]
    for f in CACHE_DIR.glob("herdr-issues*.json"):  # a new key may be another person: forget the cached lists
        f.unlink(missing_ok=True)
    return "Linear key saved"


def ask_for_github_token(screen):
    """Ask for a GitHub token and sign gh in with it. Returns a footer message."""
    token = ask_for_one_line(screen, "setup  ·  GitHub token", "enter save · esc back",
                ("github.com → settings → developer settings → personal access tokens, scopes: repo, read:org",
                 "or run  gh auth login  in a terminal instead. replaces the current login."))
    if not token:
        return ""
    out = subprocess.run(["gh", "auth", "login", "--with-token"], input=token, capture_output=True, text=True)
    return f"gh said no: {(out.stderr or out.stdout).strip()}"[:120] if out.returncode else "gh signed in"


def run_setup(screen, force=False):
    try:
        return run_setup_checklist(screen, force)
    finally:
        UiState.grey_out_behind_dialog = False


def run_setup_checklist(screen, force=False):
    """Checklist hosted by Col. The quick checks show at once, the connector rows fill in from a thread.
    enter fixes the first missing item (paste a secret, or shows what to do), L / G replace the Linear key or the
    GitHub token at any time, r re-checks, esc goes on. force: opened by hand, so it stays open when all is well."""
    quick_checks, connector_checks, frame_count, message, passed_at = quick_setup_checks(), {}, 0, "", 0

    def look():
        connector_checks.clear()
        threading.Thread(target=lambda: connector_checks.__setitem__("rows", connector_setup_checks()), daemon=True).start()
    look()
    while True:
        frame_count += 1
        all_checks = quick_checks + connector_checks.get("rows", [(f"{connector_name} connector in Claude Code", None, "") for connector_name in ("Linear", "Slack")])
        missing = [check for check in all_checks if not check[1]]
        if not missing and not passed_at:
            write_stored_value("setup", {"ok": datetime.now().isoformat()})
            passed_at = time.time()
            screen.timeout(120)
        barking = passed_at and time.time() < passed_at + 1.5  # a 1.5s woof when everything is green
        if passed_at and not barking:
            screen.timeout(250)
            if not force:
                return True
        face = COL_FACES[("woof" if frame_count // 2 % 2 else "cheer") if barking else "cheer"]
        UiState.grey_out_behind_dialog = bool(missing)
        line, box_width = draw_dialog_box(screen, "setup  ·  what Col needs", 11 + len(all_checks), 104)
        if UiState.draw_screen_behind_dialog is None:
            for line_number, face_line in enumerate(face):
                line(2 + line_number, face_line, curses.color_pair(DEFAULT_TEXT) | curses.A_BOLD, 3)
        else:
            COL_SPEECH.update(mood="woof" if barking else "cheer",
                           say="all set" if not missing else "a few things first", until=time.time() + 2)
        line(3, ("WOOF! all set." if frame_count // 2 % 2 else "woof. all set.") if barking else
             "all set. swap a credential below, or esc." if not missing else "hi, I'm Col. a few things before we start.",
             curses.color_pair(ACCENT) | curses.A_BOLD, 17)
        line(4, message or f"{len(all_checks) - len(missing)} of {len(all_checks)} ready", curses.color_pair(GREEN if "saved" in message or "signed" in message
                                                                                                else RED if message else MUTED), 17)
        for line_number, (name, is_ok, how_to_fix) in enumerate(all_checks):
            mark = "✓" if is_ok else SPINNER_FRAMES[frame_count % len(SPINNER_FRAMES)] if is_ok is None else "✗"
            line(7 + line_number, mark, curses.color_pair(GREEN if is_ok else MUTED if is_ok is None else RED) | curses.A_BOLD, 4)
            line(7 + line_number, name, curses.color_pair(DEFAULT_TEXT if is_ok else MUTED if is_ok is None else RED) | (curses.A_BOLD if is_ok is False else 0), 6)
            if is_ok is None:
                line(7 + line_number, "checking…", curses.color_pair(DIM), 46)
            elif not is_ok:
                line(7 + line_number, ("F: point it at your folder" if name.startswith("your repositories") else "enter: paste it here")
                     if how_to_fix == "paste" else how_to_fix, curses.color_pair(DIM), 46)
            elif how_to_fix == "paste":
                line(7 + line_number, f"{saved_user_name()}  ·  N: change it" if name.startswith("your name") else "L: new key" if name.startswith("Linear")
                     else "F: another folder" if name.startswith("your repositories") else "G: new token", curses.color_pair(DIM), 46)
        draw_footer_from_text(screen, ("l/enter fix the first ✗ · " if missing else "") + "N your name · L new Linear key · G new GitHub token · F repositories folder · r check again · esc/h "
                + ("close" if not missing else "skip for now"))
        screen.refresh()
        try:
            key = screen.getch()  # 250ms timeout: the spinner turns while the connectors are checked
        except curses.error:
            continue
        if key in (27, ord("h")):
            return not missing
        if key in (10, 13, curses.KEY_ENTER, ord("l")) and missing and missing[0][1] is False and missing[0][2] == "paste":
            first_missing = missing[0][0]
            key = ord("N") if first_missing.startswith("your name") else ord("L") if first_missing.startswith("Linear") else \
                ord("F") if first_missing.startswith("your repositories") else ord("G")
        ask_by_key = {ord("N"): ask_for_user_name, ord("L"): ask_for_linear_api_key, ord("G"): ask_for_github_token, ord("F"): ask_for_repositories_folder}
        if key == ord("r") or key in ask_by_key:
            message = ask_by_key[key](screen) if key in ask_by_key else ""
            quick_checks, passed_at = quick_setup_checks(), 0
            if key == ord("r"):
                look()


def issue_as_prompt_text(issue):
    """The issue as plain text for a question prompt: status, assignee, description, comments."""
    issue_details = linear_graphql("query($id: String!) { issue(id: $id) { description comments(first: 50) { nodes { body createdAt "
            "user { displayName } } } } }", {"id": issue["id"]})["issue"]
    comments = sorted(issue_details["comments"]["nodes"], key=lambda comment: comment["createdAt"])
    return "\n".join([f"Issue {issue['identifier']}: {issue['title']}",
                      f"Status: {issue['state']['name']} · Assignee: {(issue.get('assignee') or {}).get('displayName', 'nobody')}",
                      f"PRs: {', '.join(u for u, _ in linked_pull_requests(issue)) or 'none'}", "", "Description:",
                      issue_details["description"] or "(empty)", "", "Comments:"] +
                     [f"- {(comment['user'] or {}).get('displayName', '?')} ({comment['createdAt'][:10]}): {comment['body']}" for comment in comments]
                     or ["(none)"])


INLINE_MARKDOWN_PATTERN = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)")


def markdown_to_styled_lines(text, width):
    """Markdown -> wrapped lines, each a list of (text, style) with style in
    "", "b", "i", "code", "head", "link", "mut". Handles headings, bold/italic, `code`, fenced blocks, lists,
    quotes and rules. ponytail: no tables or nested emphasis, rendered as plain text."""
    styled_lines, inside_code_block = [], False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("```"):
            inside_code_block = not inside_code_block
            continue
        if inside_code_block:  # code blocks keep their layout, cut at the box edge instead of wrapping
            styled_lines.append([("  " + raw_line[:width - 2], "code")])
            continue
        if re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", raw_line):
            styled_lines.append([("─" * width, "mut")])
            continue
        if match := re.match(r"#{1,6}\s+(.*)", raw_line):
            styled_lines += [[(wrapped_text, "head")] for wrapped_text in textwrap.wrap(match.group(1).strip("*"), width)]
            continue
        first_line_prefix, later_lines_prefix, line_text, base_style = "", "", raw_line.strip(), ""
        if match := re.match(r"(\s*)([-*+]|\d+[.)])\s+(.*)", raw_line):
            indent = " " * (len(match.group(1)) // 2 * 2)
            bullet = "•" if match.group(2) in "-*+" else match.group(2)
            first_line_prefix, later_lines_prefix, line_text = f"{indent}{bullet} ", " " * (len(indent) + len(bullet) + 1), match.group(3)
        elif match := re.match(r"\s*>\s?(.*)", raw_line):
            first_line_prefix, later_lines_prefix, line_text, base_style = "│ ", "│ ", match.group(1), "mut"
        segments = []
        for piece in INLINE_MARKDOWN_PATTERN.split(line_text):
            if not piece:
                continue
            if piece[:2] in ("**", "__") and len(piece) > 4:
                inner_text = piece[2:-2]  # **`code`** is common in claude's answers: show it as code
                segments.append((inner_text[1:-1], "code") if inner_text[:1] == inner_text[-1:] == "`" else (inner_text, "b"))
            elif piece[0] == "`":
                segments.append((piece[1:-1], "code"))
            elif piece[0] == "[" and (match := re.fullmatch(r"\[([^\]]+)\]\([^)]+\)", piece)):
                segments.append((match.group(1), "link"))
            elif piece[0] in "*_" and len(piece) > 2 and piece[-1] == piece[0]:
                segments.append((piece[1:-1], "i"))
            else:
                segments.append((piece, base_style))
        styled_lines += wrap_styled_segments(segments, width, first_line_prefix, later_lines_prefix, base_style) if segments else [[]]
    return styled_lines


def wrap_styled_segments(segments, width, first_line_prefix, later_lines_prefix, base_style=""):
    """Greedy word wrap of styled segments; later lines start with hang (list/quote indent)."""
    words = []  # (word with its trailing space, style)
    for text, style in segments:
        if text[:1].isspace() and words:  # the space between this segment and the previous one
            words[-1] = (words[-1][0].rstrip() + " ", words[-1][1])
        words += [(matched_word, style) for matched_word in re.findall(r"\S+\s*", text)]
    lines, current_line, current_line_width = [], [(first_line_prefix, base_style)] if first_line_prefix else [], len(first_line_prefix)
    for word, style in words:
        if current_line_width + len(word.rstrip()) > width and current_line_width > len(first_line_prefix if not lines else later_lines_prefix):
            lines.append(current_line)
            current_line, current_line_width = [(later_lines_prefix, base_style)] if later_lines_prefix else [], len(later_lines_prefix)
        current_line.append((word, style))
        current_line_width += len(word)
    return lines + [current_line]


# Read-only: anything not listed here is denied, since -p can't ask for permission.
SOURCES_COL_CAN_USE = ("sources: the issue and its comments · Linear (related issues, projects, docs) · Slack (search, channels, "
           "threads · can post when you ask) · GitHub via gh (PRs, reviews, CI) · git history · the web.  Linear is read-only")
LINEAR_TOOLS_COL_MAY_USE = ("get_issue list_issues list_comments get_project list_projects get_document list_documents "
                            "get_attachment extract_images get_user list_users get_team list_teams get_milestone list_milestones "
                            "list_cycles get_initiative list_initiatives list_issue_statuses list_issue_labels get_status_updates").split()
SLACK_TOOLS_COL_MAY_USE = ("slack_search_public slack_search_public_and_private slack_read_channel slack_read_thread "
                           "slack_search_channels slack_search_users slack_read_user_profile slack_read_canvas "
                           "slack_send_message slack_send_message_draft").split()
COMMANDS_COL_MAY_RUN = ("git log", "git show", "git diff", "git status", "git branch", "gh pr view", "gh pr list",
                        "gh pr checks", "gh pr diff", "gh run view", "gh run list", "gh issue view", "gh search prs")


def tools_col_may_use(connectors=None):
    """The allow-list for Col's headless claude: read tools of the Linear server, the Slack tools, the web, and
    read-only git and gh commands. The two servers are addressed by the names setup found on this machine."""
    connectors = connectors or connector_names()
    return ([mcp_tool_prefix(connectors["Linear"]) + tool for tool in LINEAR_TOOLS_COL_MAY_USE]
            + [mcp_tool_prefix(connectors["Slack"]) + tool for tool in SLACK_TOOLS_COL_MAY_USE]
            + ["WebSearch", "WebFetch"] + [f"Bash({command}:*)" for command in COMMANDS_COL_MAY_RUN])


def describe_tool_call(tool):
    """Spinner text for a tool call from claude's stream."""
    name, tool_input = tool["name"], tool.get("input") or {}
    if name.startswith("mcp__"):  # "Linear: get issue", whatever the server is called here
        server_part, _, action = name[len("mcp__"):].partition("__")
        service = next((service for service, server_name in connector_names().items() if mcp_tool_prefix(server_name) == f"mcp__{server_part}__"),
                       server_part.rpartition("_")[2])
        return f"{service}: {action.removeprefix('slack_').replace('_', ' ')}"
    return {"Bash": tool_input.get("command", ""), "WebSearch": f"searching {tool_input.get('query', '')}",
            "WebFetch": f"fetching {tool_input.get('url', '')}"}.get(name, name)


ASK_ABOUT_ISSUE_TASK = ("Answer questions about one Linear issue. Its text is below. When that isn't enough, look things up: "
            "Linear for related, parent and sub-issues, projects and documents; the web for docs and linked pages; "
            "Slack for discussions, decisions and who said what (search, channels, threads); "
            "git and gh (read-only) for PRs, review comments, CI and commits. Look up only what the question needs. "
            "You can't change the issue or the code. You may post to Slack, but only when the question explicitly "
            "asks you to send a message, and then show what you sent.\n"
            "Answer briefly and concretely: a few sentences or bullets, Markdown is fine. Say where a fact came from "
            "when it isn't the issue. If you can't find out, say so.", "Question", "Answer")
ASK_ABOUT_REPORT_TASK = ("You are Col, a border collie who wrote the developer's daily report below. Answer their questions about it: "
               "what a line means, what happened in a thread, what to do first. The report's sources are the issues "
               "listed under it; look things up when the report isn't enough: Linear (comments, issues), the linked PRs "
               "with gh pr view <url>, Slack (search, read threads). You may post to Slack only when explicitly asked, "
               "and then show what you sent. Linear and code stay read-only.\n"
               "Answer briefly and concretely, a few sentences or bullets, Markdown is fine. Say where a fact came from "
               "when it isn't in the report. If you can't find out, say so.", "Question", "Col")
DRAFT_COMMENT_TASK = ("Draft a comment on one Linear issue, to be posted by its author (the person asking you). The issue is "
              "below, then a brief for what the comment should say; later briefs are feedback on your previous draft. "
              "Look things up only if the brief needs a fact you don't have: Linear, the web, git and gh (read-only).\n"
              "Write in first person as the author, short and plain, concrete, no filler, no greeting or sign-off. "
              "Markdown is fine. Output only the comment body, nothing before or after it.", "Brief", "Draft")


def ask_headless_claude(context, history, cwd, progress, running_processes, task=ASK_ABOUT_ISSUE_TASK):
    """Headless claude on the issue, with read-only Linear, web and git/gh tools (see tools_col_may_use()).
    User settings are loaded: without them the claude.ai connectors (Linear, Slack) are invisible. progress(text) gets each tool call.
    task: (instructions, label for my turns, label for its turns), see ASK_ABOUT_ISSUE_TASK and DRAFT_COMMENT_TASK."""
    instructions, label_for_my_turns, label_for_its_turns = task
    prompt = (f"{instructions}\nYou're in {cwd}.\n\n" + context + "\n\n"
              + "".join(f"{label_for_my_turns}: {earlier_question}\n{label_for_its_turns}: {earlier_answer}\n\n" for earlier_question, earlier_answer in history[:-1])
              + f"{label_for_my_turns}: {history[-1][0]}\n{label_for_its_turns}:")
    # --tools limits the built-ins only; "" would also hide the MCP connectors
    process = subprocess.Popen(["claude", "-p", "--model", "sonnet", "--setting-sources", "user", "--no-session-persistence",
                             "--tools", "Bash,WebSearch,WebFetch", "--allowedTools", *tools_col_may_use(),
                             "--output-format", "stream-json", "--verbose"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
    running_processes.append(process)  # so esc can kill it
    process.stdin.write(prompt)
    process.stdin.close()
    result = ""
    for raw_line in process.stdout:
        try:
            event = json.loads(raw_line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for content_part in event["message"]["content"]:
                if content_part["type"] == "tool_use":
                    progress(describe_tool_call(content_part))
        elif event.get("type") == "result":
            result = event.get("result") or ""
    process.wait()
    return result.strip() or f"claude failed: {process.stderr.read().strip()[-200:]}"


WRITE_DAILY_REPORT_TASK = (
    "You are Col, a border collie who herds this developer's work. Write their morning brief: what they've been on for "
    "the last two days and how it's going, so they can pick the threads back up. Below: today's date, their in-progress "
    "Linear issues, and the herdr workspaces open on their machine. Look up what actually happened, briefly: Linear "
    "comments and status on those issues; the linked PRs with gh pr view <url> (there is no repo in the cwd); Slack for discussions "
    "and decisions (search the issue ids and their name, read the threads that matter). At most 8 lookups.\n"
    "Format, Markdown. Three sections, each a heading line then bullets, at most 5 bullets per section:\n"
    "## on your plate  — one bullet per issue: **PLA-123 two-word nickname** then an em dash, then where it stands in "
    "one plain sentence with one concrete fact (a PR number, a date, a name, a number). Max 25 words per bullet. "
    "Then, on its own line right under the bullet (not a sub-bullet), who's on it: '↳ only you', or '↳ you and Sam' "
    "naming the people active on it in the last two days: comment authors, PR reviewers, people in the Slack thread.\n"
    "## loose ends  — replies owed, reviews waiting, blocked or gone quiet. One short sentence each.\n"
    "## today  — one bullet: where to start, and why, in one sentence.\n"
    "Playful but concise, no filler, no sub-bullets, no nested emphasis. Address them as you, and by first name once "
    "or twice (the developer line below has it). Say nothing you couldn't "
"source; if Slack or Linear had nothing, say so in a few words.", "Request", "Brief")
DAILY_REPORT = {"procs": []}  # today's brief: date, text (None while being written), steps (lookups so far)


def daily_report_context(issues):
    mine = [i for i in issues if i["state"]["type"] == "started"]
    who = saved_user_name() or next(((i.get("assignee") or {}).get("displayName") for i in issues if i.get("assignee")), "me")
    now = datetime.now(timezone.utc)
    lines = [f"today: {datetime.now():%A %Y-%m-%d}", f"developer: {who}", "", "issues in progress or review:"]
    lines += [f"- {i['identifier']}  {i['title']}  ({i['state']['name']}, updated {time_since_text(i['updatedAt'], now)})"
              + "".join(f"\n    PR: {url}" for url, _ in linked_pull_requests(i)) for i in mine] or ["- none"]
    lines += ["", "herdr workspaces open on this machine:"] + ([f"- {workspace['label']}" for workspace in open_herdr_workspaces()] or ["- none"])
    return "\n".join(lines)


def start_writing_daily_report(issues, today):
    """Write today's brief in a thread, once. The result is cached for the day."""
    if DAILY_REPORT.get("date") == today:
        return
    DAILY_REPORT.update(date=today, text=None, steps=[])

    def work():
        try:
            text = ask_headless_claude(daily_report_context(issues), [("Write today's brief.", "")], str(Path.home()), DAILY_REPORT["steps"].append,
                          DAILY_REPORT["procs"], WRITE_DAILY_REPORT_TASK)
        except Exception as e:
            text = f"Col couldn't write it: {e}"
        DAILY_REPORT["text"] = text
        write_stored_value("brief", {"date": today, "text": text, "steps": DAILY_REPORT["steps"]})
    threading.Thread(target=work, daemon=True).start()


daily_report_text_attributes = lambda: dict(TEXT_ATTRIBUTE_BY_STYLE, code=curses.color_pair(CODE), head=curses.color_pair(ACCENT) | curses.A_BOLD,
                            b=curses.color_pair(ACCENT) | curses.A_BOLD, mut=curses.color_pair(MUTED), dim=curses.color_pair(DIM),
                            tag=curses.color_pair(TITLE_TAG) | curses.A_BOLD, rule=curses.color_pair(BORDER))


def daily_report_as_styled_lines(text, width):
    """The report as (text, style) lines, easy to scan: each section is a chip with a rule under it, and every
    bullet (one topic) sits in its own block with a thin rule between topics."""
    lines = []
    for segments in markdown_to_styled_lines(text, width):
        if segments and segments[0][1] == "head":
            lines += ([[], []] if lines else []) + [[(f" {segments[0][0].upper()} ", "tag")], [("─" * width, "rule")], []]
        elif segments and segments[0][0].startswith("•"):
            while lines and not lines[-1]:
                lines.pop()
            if lines and lines[-1][0][1] != "rule":  # a topic follows a topic: a thin line between them
                lines += [[], [("╌" * width, "rule")]]
            lines += [[], segments]
        elif segments and segments[0][0].lstrip().startswith("↳"):  # who's on it: muted, indented under the bullet
            lines.append([("  " + segment_text.lstrip() if segment_number == 0 else segment_text, "mut") for segment_number, (segment_text, _) in enumerate(segments)])
        else:
            lines.append(segments)
    return lines


class ReportTopic(NamedTuple):
    """One bullet of the daily report."""
    number: int             # its place among the report's topics, from 0
    text: str               # the bullet's first line, without the bullet
    issue_identifier: str   # the first issue id it mentions (PLA-123), "" when it mentions none


def report_topic_under_cursor(report_text):
    """The topic the cursor is on in the daily report, or None when the report has no bullets."""
    lines = daily_report_as_styled_lines(report_text, 80)
    bullet_lines = [n for n, segments in enumerate(lines) if segments and segments[0][0].startswith("•")]
    if not bullet_lines:
        return None
    number = max(0, min(DAILY_REPORT.get("cur", 0), len(bullet_lines) - 1))
    text = "".join(part for part, _ in lines[bullet_lines[number]]).lstrip("• ").strip()
    mentioned = ISSUE_IDENTIFIER_PATTERN.search(text)
    return ReportTopic(number, text, mentioned.group() if mentioned else "")


def col_words_for_lookup(step):
    """Col's words for one lookup from the agent's stream."""
    if step.startswith("Linear"):
        return "nosing through Linear: " + step[8:]
    if step.startswith("Slack"):
        return "sniffing around Slack: " + step[7:]
    if "gh " in step:
        return "digging up the PRs on GitHub"
    if step.startswith("git"):
        return "pawing through git history"
    if step.startswith("Web"):
        return "sniffing the web"
    return "chewing on it"


def show_daily_report(screen, issues, today):
    """Col's recap of the last two days. Shows the lookups while it's being written; j/k move between topics, l goes
    to the topic's issue (returns its identifier), esc, h or b close (returns None). The topic is remembered."""
    start_writing_daily_report(issues, today)
    first_line_shown, frame_count = 0, 0
    attribute_by_style = daily_report_text_attributes()
    draw_list_behind = UiState.draw_screen_behind_dialog

    topic_position_on_screen = [0, 0, 0]  # the chosen topic on screen: its first row, the row under it, its x. the actions drop down there

    def paint():
        """The report, without refresh; also the backdrop of the topic's actions."""
        nonlocal first_line_shown, frame_count
        frame_count += 1
        screen_height, screen_width = screen.getmaxyx()
        box_height = screen_height - 10
        line, box_width = draw_dialog_box(screen, "Col  ·  daily report", box_height, min(screen_width - 6, 100))
        masthead = "^ ▐▌ ^  T H E   D A I L Y   B A R K"  # the report's masthead, on the box's top edge: Col's paper, like the app's badge
        if box_width > len(masthead) + 8:
            line(0, "  " + masthead + "  ", curses.color_pair(COL_ACCENT) | curses.A_REVERSE | curses.A_BOLD, (box_width - len(masthead)) // 2 - 2)
            line(0, "^ ▐▌ ^", curses.color_pair(COL_BADGE) | curses.A_BOLD, (box_width - len(masthead)) // 2)
        report_text, lookup_steps = DAILY_REPORT["text"], DAILY_REPORT["steps"]
        UiState.col_is_working = report_text is None
        if report_text is None:  # loading: Col in the corner looks around and says what he's sniffing at; the trail is in the box
            UiState.lookups_col_is_working_on = lookup_steps
            line(3, f"{SPINNER_FRAMES[frame_count % len(SPINNER_FRAMES)]} writing your daily report…", curses.color_pair(ACCENT) | curses.A_BOLD, 3)
            steps_so_far = ["reading your issues and open workspaces"] + [col_words_for_lookup(step) for step in lookup_steps]
            shown_steps = steps_so_far[-(box_height - 8):]
            for row_offset, step in enumerate(shown_steps):
                is_last_step = row_offset == len(shown_steps) - 1
                spinner_or_tick = SPINNER_FRAMES[frame_count % len(SPINNER_FRAMES)] if is_last_step else "✓"
                line(5 + row_offset, spinner_or_tick + "  " + step,
                     curses.color_pair(ACCENT if is_last_step else DIM) | (curses.A_BOLD if is_last_step else 0), 4)
            draw_footer_from_text(screen, "r start over · esc/h close, b brings it back when it's done")
        else:
            if COL_SPEECH["mood"] in ("left", "right") or not COL_SPEECH["say"]:
                COL_SPEECH.update(mood="cheer", say="here's your daily report", until=time.time() + 8)
            line(2, f"{greeting_for_this_hour()}, {saved_first_name() or 'you'}. here's what you've been up to.", curses.color_pair(COL_ACCENT) | curses.A_BOLD, 3)
            line(3, f"{len(lookup_steps)} lookups in Linear, Slack and GitHub" if lookup_steps else "from memory", curses.color_pair(DIM), 3)
            styled_lines, rows_for_text = daily_report_as_styled_lines(report_text, min(box_width - 8, 88)), box_height - 8  # the row under the text holds the "go to" marker
            # the topics: each bullet is one; j/k move between them, l jumps to its issue in the list
            bullet_line_numbers = [line_number for line_number, segments in enumerate(styled_lines) if segments and segments[0][0].startswith("•")]
            number_of_topic_under_cursor = max(0, min(DAILY_REPORT.get("cur", 0), len(bullet_line_numbers) - 1))
            DAILY_REPORT["cur"] = number_of_topic_under_cursor
            if bullet_line_numbers:
                first_line_of_topic = bullet_line_numbers[number_of_topic_under_cursor]
                line_after_topic = next((line_number for line_number in range(first_line_of_topic + 1, len(styled_lines))
                                         if not styled_lines[line_number] or styled_lines[line_number][0][1] == "rule"), len(styled_lines))
                if first_line_of_topic < first_line_shown or line_after_topic > first_line_shown + rows_for_text:  # keep the chosen topic in view
                    first_line_shown = max(0, min(first_line_of_topic - 2, len(styled_lines) - rows_for_text))
            first_line_shown = max(0, min(first_line_shown, len(styled_lines) - rows_for_text))
            if bullet_line_numbers:
                topic_position_on_screen[:] = line.first_row_on_screen + 5 + first_line_of_topic - first_line_shown, line.first_row_on_screen + 5 + min(line_after_topic - first_line_shown, rows_for_text), line.first_column_on_screen + 6
            for row_offset, segments in enumerate(styled_lines[first_line_shown:first_line_shown + rows_for_text]):
                line_number, column = first_line_shown + row_offset, 4
                is_in_topic_under_cursor = bullet_line_numbers and bullet_line_numbers[number_of_topic_under_cursor] <= line_number < line_after_topic
                if segments and bullet_line_numbers and line_number >= bullet_line_numbers[0]:  # click a topic: the cursor goes there; click it again: its actions
                    number_of_topic_on_this_line = sum(bullet_line <= line_number for bullet_line in bullet_line_numbers) - 1
                    register_clickable_row(line.first_row_on_screen + 5 + row_offset, 0, screen_width,
                                           number_of_topic_on_this_line - number_of_topic_under_cursor, [10] * bool(is_in_topic_under_cursor))
                if is_in_topic_under_cursor:  # cursorline, like the list
                    line(5 + row_offset, " " * (box_width - 4), curses.color_pair(SELECTED_ROW), 2)
                    line(5 + row_offset, "▎", curses.color_pair(SELECTED_ROW_MARKER), 2)
                for segment_text, style in segments:
                    cursor_line_attribute = curses.color_pair(SELECTED_ROW) | (curses.A_BOLD if style in ("b", "head") else 0)
                    line(5 + row_offset, segment_text, cursor_line_attribute if is_in_topic_under_cursor else attribute_by_style[style], column)
                    column += len(segment_text)
            if bullet_line_numbers:
                mentioned_identifier = ISSUE_IDENTIFIER_PATTERN.search("".join(segment_text for segment_text, _ in styled_lines[bullet_line_numbers[number_of_topic_under_cursor]]))
                footer_text = ("l/enter actions" + (f" for {mentioned_identifier.group()}" if mentioned_identifier else " · n create a Linear issue from this topic")
                        + " · c clean slate on it · a ask Col about it · j/k topics · d/u page · r write it again")
                draw_footer_from_text(screen, footer_text + " · esc/h close")

    def under_menu():  # draw_dialog_box() paints the backdrop first: hand it the list while the report draws itself, no pop-open
        UiState.draw_screen_behind_dialog, title_before, UiState.title_of_open_dialog = draw_list_behind, UiState.title_of_open_dialog, "Col  ·  daily report"
        paint()
        UiState.draw_screen_behind_dialog, UiState.title_of_open_dialog = under_menu, title_before

    while True:
        paint()
        report_text = DAILY_REPORT["text"]
        screen.refresh()
        try:
            key = screen.getch()
        except curses.error:
            continue
        if key in (27, ord("h"), ord("q"), ord("b")):
            UiState.col_is_working = False
            return None
        topic = report_topic_under_cursor(report_text) if report_text else None
        my_issue_identifiers = {listed_issue["identifier"] for listed_issue in issues}
        if topic and key in (ord("l"), 10, 13, curses.KEY_ENTER):  # the topic's actions: pick one, or press its key
            identifier = topic.issue_identifier
            choices = [("l", f"go to {identifier} in the list") if identifier in my_issue_identifiers else
                       ("l", f"start working on {identifier}  ·  not yours yet, it gets assigned to you") if identifier else
                       ("n", "create a Linear issue from this topic"),
                       ("c", "clean slate on it"), ("a", "ask Col about it")]
            rows_the_menu_needs = len(choices) + 6
            row_for_the_menu = topic_position_on_screen[1] if topic_position_on_screen[1] + rows_the_menu_needs <= screen.getmaxyx()[0] - 6 else topic_position_on_screen[0] - rows_the_menu_needs
            UiState.draw_screen_behind_dialog = under_menu  # the report stays up behind the menu
            try:  # the menu opens right under the topic, or above it when there's no room
                picked = choose_from_list(screen, "Col  ·  " + (identifier or "this topic"), [f"{k}   {choice_text}" for k, choice_text in choices],
                                          keys="".join(k for k, _ in choices), top_left_corner=(row_for_the_menu, topic_position_on_screen[2]))
            finally:  # and the report doesn't pop open again when the menu closes
                UiState.draw_screen_behind_dialog, UiState.title_of_open_dialog = draw_list_behind, "Col  ·  daily report"
            if picked is None:
                continue
            key = ord(choices[picked][0])  # from here on it is as if that short key was pressed
        if topic and key == ord("a"):
            chat_with_col(screen, None, report=report_text, issues=issues, topic=(topic.number, topic.text))
        elif topic and key == ord("c") and topic.issue_identifier:  # clean slate on the issue, yours or not
            try:
                issue = next((listed_issue for listed_issue in issues if listed_issue["identifier"] == topic.issue_identifier), None) or run_with_spinner(
                    screen, f"fetching {topic.issue_identifier}…", lambda: fetch_issue_by_identifier(topic.issue_identifier))
                UiState.col_is_working = False
                if error := start_clean_slate(screen, issue):
                    show_message_for_a_moment(screen, error)
            except Exception as error:
                show_message_for_a_moment(screen, f"couldn't fetch {topic.issue_identifier}: {error}"[:120])
        elif topic and key == ord("c"):  # no issue: a clean slate on the topic itself, in a notes folder of its own
            headline = topic.text.split(" — ")[0].strip("* ")[:60]
            stand_in_issue = {"identifier": f"topic-{datetime.now():%Y%m%d}-{topic.number + 1}", "title": headline, "url": "",
                              "branchName": "", "assignee": {"displayName": saved_user_name() or "you"}}
            UiState.col_is_working = False
            if error := start_clean_slate(screen, stand_in_issue, topic=topic.text):
                show_message_for_a_moment(screen, error)
        if key == ord("r"):  # start over: stop a running one, forget today's
            for process in DAILY_REPORT["procs"]:
                process.kill()
            DAILY_REPORT["procs"].clear()
            DAILY_REPORT["date"], first_line_shown, DAILY_REPORT["cur"] = None, 0, 0
            start_writing_daily_report(issues, today)
        if report_text and key in (ord("j"), curses.KEY_DOWN, ord("k"), curses.KEY_UP):  # next / previous topic
            DAILY_REPORT["cur"] = DAILY_REPORT.get("cur", 0) + (1 if key in (ord("j"), curses.KEY_DOWN) else -1)
            write_stored_value("brief", {k: DAILY_REPORT[k] for k in ("date", "text", "steps", "cur")})  # so b reopens where you left off
        elif topic and key in (ord("l"), ord("n")):  # go to / start / create, for the topic
            UiState.col_is_working = False
            if topic.issue_identifier in my_issue_identifiers:
                if key == ord("l"):
                    return topic.issue_identifier
            elif topic.issue_identifier and key == ord("l"):  # someone else's, or unassigned: fetch it and start, which assigns it to you
                try:
                    someone_elses_issue = run_with_spinner(screen, f"fetching {topic.issue_identifier}…",
                                                           lambda: fetch_issue_by_identifier(topic.issue_identifier))
                    if error := start_work_on_issue(screen, someone_elses_issue):
                        show_message_for_a_moment(screen, error)
                except Exception as error:
                    show_message_for_a_moment(screen, f"couldn't fetch {topic.issue_identifier}: {error}"[:120])
            elif not topic.issue_identifier and key == ord("n"):  # a topic with no issue yet: make one, the topic as its first description
                headline = topic.text.split(" — ")[0].strip("* ")[:70]
                create_issue_and_write_it(screen, title=headline, body=f"From Col's daily report, {datetime.now():%Y-%m-%d}:\n\n{topic.text}")
        first_line_shown += {ord("d"): 10, ord("u"): -10}.get(key, 0)


TEXT_ATTRIBUTE_BY_STYLE = {"": 0, "b": curses.A_BOLD, "i": curses.A_ITALIC, "link": curses.A_UNDERLINE}  # + colours, see draw_chat()


def lookup_trail_line(steps, n=4):
    """One dim line of what Claude looked at, Linear-style: "↳ looked at  Linear: get issue · gh pr view 7  +2"."""
    seen = list(dict.fromkeys(steps))
    return "↳ looked at  " + " · ".join(seen[:n]) + (f"  +{len(seen) - n}" if len(seen) > n else "") if seen else ""


def draw_chat(screen, title, issue, history, thinking, text, top, draft=False, report=None):
    """Linear-style thread: context line, then messages with author tags and times, Claude's with a gutter bar
    and a trail of what it looked at, and an input box pinned at the bottom. Returns the scroll offset used:
    top=None starts at the latest message; while thinking it sticks to the bottom.
    report: the daily report's text, shown in a pane on the left; otherwise the issue is shown there. The thread
    takes the right half, so what you're asking about stays in view."""
    screen_height, screen_width = screen.getmaxyx()
    box_height, thread_width = screen_height - 10, min(screen_width - 6, 110)
    thread_x_offset = 0  # the thread's x offset inside the frame (the right pane)
    if report or issue:
        full_box_width = min(screen_width - 6, 170)
        thread_x_offset = (full_box_width - 6) * 50 // 100 + 4
        thread_width = full_box_width - thread_x_offset
    thread_text_width = thread_width - 6
    thread_lines, first_line_of_latest_message = [], 0
    for message in history:
        first_line_of_latest_message = len(thread_lines)
        thread_lines.append([(" you ", "tag"), (f"  {message['t']}", "dim")])
        thread_lines += [[("  " + segment_text, "b")] for segment_text in textwrap.wrap(message["q"], thread_text_width - 2)]
        thread_lines.append([])
        thread_lines.append([(" ✦ col ", "ctag"), (f"  {message['t2']}" if message.get("t2") else "", "dim")])
        if message["a"] is None:  # live: finished steps ticked, the current one spinning
            for lookup_step in message["steps"][:-1]:
                thread_lines.append([("  ✓ ", "dim"), (lookup_step, "dim")])
            thread_lines.append([("  " + thinking.split(" ", 1)[0] + " ", "think"), (message["steps"][-1] if message["steps"] else "thinking…", "mut")])
        else:
            thread_lines += [[("▎ ", "gut")] + segments for segments in markdown_to_styled_lines(message["a"], thread_text_width - 2)]
            if segment_text := lookup_trail_line(message["steps"]):
                thread_lines.append([("  " + segment_text, "dim")])
        thread_lines.append([])
    rows_for_messages = box_height - 9  # rows 4 .. h-6; the input box takes h-5 .. h-3
    last_scroll_position = max(len(thread_lines) - rows_for_messages, 0)
    top = last_scroll_position if thinking or top is None and not history else min(max(first_line_of_latest_message if top is None else top, 0), last_scroll_position)
    draw_in_box, full_box_width = draw_dialog_box(screen, title, box_height, full_box_width)
    side_pane_width = thread_x_offset - 4  # side pane: x 3 .. lw+2, then a divider, then the thread
    if report:
        side_pane_lines = daily_report_as_styled_lines(report, side_pane_width - 4)
        if CHAT_SIDE_PANE["top"] is None and CHAT_SIDE_PANE.get("topic"):  # open at the topic
            start_of_topic = CHAT_SIDE_PANE["topic"][:24]
            CHAT_SIDE_PANE["top"] = next((line_number for line_number, segments in enumerate(side_pane_lines) if start_of_topic in "".join(segment_text for segment_text, _ in segments)), 1) - 1
    else:  # the issue: a Linear-ish header line, then its description
        icon, icon_colour = progress_icon_for_state(issue["state"]["name"], issue["state"]["type"])
        bars, word, priority_colour = PRIORITY_DISPLAY.get(issue["priority"], ("▯▯▯", "no priority", DIM))
        header_segments = [(f"{icon} {issue['state']['name']}", "s" + str(icon_colour)), ("   ", ""), (f"{bars} {word}", "s" + str(priority_colour)), ("   ", ""),
                (f"@ {(issue.get('assignee') or {}).get('displayName', 'unassigned')}", "mut")]
        if pull_request_badge(issue)[0]:
            header_segments += [("   ", ""), (pull_request_badge(issue)[0], "s" + str(pull_request_badge(issue)[1]))]
        side_pane_lines = [[(issue["title"][:side_pane_width - 4], "b")], header_segments, [("─" * (side_pane_width - 4), "dim")]]
        if not (draft and CHAT_SIDE_PANE["comments"] is not None):  # drafting with the thread open: the thread alone, no description
            side_pane_lines += markdown_to_styled_lines(issue.get("description") or "_no description yet_", side_pane_width - 4)
        if CHAT_SIDE_PANE["comments"] is not None:  # the comments were open in the preview: keep them in view
            if CHAT_SIDE_PANE["top"] is None:
                CHAT_SIDE_PANE["top"] = 10 ** 6 if draft else len(side_pane_lines) + 1  # drafting: the newest comments; asking: the comments, description a ctrl-u up
            side_pane_lines += [[], [(f" COMMENTS  {len(CHAT_SIDE_PANE['comments'])} ", "tag")], []]
            for comment_author, comment_body in CHAT_SIDE_PANE["comments"]:
                side_pane_lines += [[(comment_author, "head")]] + markdown_to_styled_lines(comment_body, side_pane_width - 4) + [[]]
    side_pane_attributes = daily_report_text_attributes()
    CHAT_SIDE_PANE["top"] = max(0, min(CHAT_SIDE_PANE["top"] or 0, len(side_pane_lines) - (box_height - 4)))
    for row, segments in enumerate(side_pane_lines[CHAT_SIDE_PANE["top"]:CHAT_SIDE_PANE["top"] + box_height - 4]):
        column = 3
        for segment_text, style in segments:
            attr = curses.color_pair(int(style[1:])) | curses.A_BOLD if style.startswith("s") else side_pane_attributes[style]
            draw_in_box(2 + row, segment_text[:max(side_pane_width - column, 0)], attr, column)
            column += len(segment_text)
    if len(side_pane_lines) > box_height - 4:
        draw_in_box(box_height - 2, f" {CHAT_SIDE_PANE['top'] + 1}/{len(side_pane_lines)} ", curses.color_pair(DIM), side_pane_width - 12)
    for row in range(2, box_height - 2):
        draw_in_box(row, "│", curses.color_pair(BORDER), thread_x_offset - 2)
    line = lambda row, segment_text, attr=0, column=0: draw_in_box(row, segment_text[:max(thread_width - column - 2, 0)], attr, column + thread_x_offset)
    thread_width = full_box_width - thread_x_offset
    line(2, ("✦ ask Col about this topic" if CHAT_SIDE_PANE.get("topic") else "✦ ask Col about the report") if report else
         f"✦ Col drafts a comment on {issue['identifier']}" if draft else f"✦ ask Col about {issue['identifier']}", curses.color_pair(ACCENT) | curses.A_BOLD, 3)
    line(3, "─" * (thread_width - 6), curses.color_pair(BORDER), 3)
    attribute_by_style = dict(TEXT_ATTRIBUTE_BY_STYLE, code=curses.color_pair(CODE), head=curses.color_pair(ACCENT) | curses.A_BOLD,
                 mut=curses.color_pair(MUTED), dim=curses.color_pair(DIM), think=curses.color_pair(ACCENT) | curses.A_BOLD,
                 tag=curses.color_pair(TITLE_TAG) | curses.A_BOLD, gut=curses.color_pair(DIM),
                 ctag=curses.color_pair(ACCENT) | curses.A_BOLD | curses.A_REVERSE)
    if not history:
        intro_text = ((f"Ask Col about this topic: {CHAT_SIDE_PANE['topic'][:90]}… Col has the report, your issues, and can look things up "
                  "in Linear, Slack and GitHub. The thread is kept for the day, one per topic." if CHAT_SIDE_PANE.get("topic") else
                  "Ask Col anything about the report: what a line means, what happened in a thread, what to do first. "
                  "Col has the report, your issues, and can look things up in Linear, Slack and GitHub. The thread is "
                  "kept for the day.") if report else
                 "Ask anything about the issue. Col reads it, its comments and linked PRs, and can look things up in "
                 "Linear, Slack, GitHub, git and the web. It can also post to Slack when you ask it to. Linear and code "
                 "stay read-only. The thread is kept with the issue." if not draft else
                 "Say what the comment should get across. Col drafts it in your voice from the issue; send feedback "
                 "until it's right, then post it. Nothing is posted until you confirm.")
        for row, segment_text in enumerate(textwrap.wrap(intro_text, thread_width - 10)):
            line(5 + row, segment_text, curses.color_pair(MUTED), 4)
    for row, segments in enumerate(thread_lines[top:top + rows_for_messages]):
        column = 3
        for segment_text, style in segments:
            if column < thread_width - 2:
                line(4 + row, segment_text[:thread_width - 2 - column], attribute_by_style[style], column)
            column += len(segment_text)
    if top > 0:
        line(4, " ↑ ", curses.color_pair(DIM), thread_width - 7)
    if top < last_scroll_position:
        line(3 + rows_for_messages, " ↓ ", curses.color_pair(DIM), thread_width - 7)
    # input box, pinned
    input_box_width, input_box_border_colour = thread_width - 4, curses.color_pair(ACCENT if not thinking else BORDER)
    line(box_height - 5, "╭" + "─" * (input_box_width - 2) + "╮", input_box_border_colour, 2)
    line(box_height - 4, "│" + " " * (input_box_width - 2) + "│", input_box_border_colour, 2)
    line(box_height - 3, "╰" + "─" * (input_box_width - 2) + "╯", input_box_border_colour, 2)
    draw_footer_from_text(screen, "esc stop · keep typing meanwhile" if thinking else ("enter send feedback · empty-enter post it" if draft else "enter send")
            + " · ↑/↓ scroll the thread · pgup/pgdn faster" + (" · ctrl-n new thread" if history else "") + " · ctrl-d/u scroll the side · esc close")
    line(box_height - 4, "›", curses.color_pair(ACCENT) | curses.A_BOLD, 4)
    if text or not thinking:
        line(box_height - 4, f"{text}▏"[-(input_box_width - 8):] if text or not thinking else "", curses.A_BOLD, 6)
    if not text and not thinking:
        line(box_height - 4, "give feedback on the draft…" if draft and history else "write a brief…" if draft else
             "ask a follow-up…" if history else "ask a question…", curses.color_pair(DIM), 7)
    screen.refresh()
    return top


def post_comment_to_linear(issue, body):
    linear_graphql("mutation($id: String!, $b: String!) { commentCreate(input: {issueId: $id, body: $b}) { success } }",
        {"id": issue["id"], "b": body})


def chat_with_col(screen, issue, draft=False, report=None, issues=(), topic=None):
    """Thread with Claude about the issue, typed straight into the pinned input: enter sends, ↑↓/PgUp/PgDn
    scroll, esc stops a running answer or closes. draft: Claude writes a comment from the brief, feedback makes
    a new draft, enter on an empty input posts the latest draft (after a confirm).
    report: a thread with Col about the daily report instead (issue is None), shown beside it, kept per day."""
    if report:
        topic_number, CHAT_SIDE_PANE["topic"] = (topic[0], topic[1]) if topic else (None, None)
        title = "Col  ·  daily report" + (f" · topic {topic_number + 1}" if topic else "")
        thread_file = NOTES_DIR / "daily" / (f"{datetime.now():%Y-%m-%d}" + (f"-topic{topic_number + 1}" if topic else "") + ".json")
        CHAT_SIDE_PANE["top"] = None  # the side pane opens at the topic
    else:
        title = f"{issue['identifier']}  ·  " + ("comment · Col drafts" if draft else "ask Col")
        thread_file = NOTES_DIR / issue["identifier"] / ("draft.json" if draft else "ask.json")  # the thread, kept with the issue's notes
    try:
        history = [message for message in json.loads(thread_file.read_text()) if message.get("a")]
    except (OSError, ValueError):
        history = []
    prompt_context, text, top = {}, "", None
    if CHAT_SIDE_PANE["top"] is not None:
        CHAT_SIDE_PANE["top"] = 0
    if report:
        CHAT_SIDE_PANE["comments"] = None
    else:
        CHAT_SIDE_PANE["topic"] = None
    while True:
        top = draw_chat(screen, title, issue, history, None, text, top, draft, report)
        try:
            key = screen.get_wch()  # wide chars, so å/ä/ö work
        except curses.error:  # the 250ms timeout main() sets
            continue
        if key == "\x1b":
            CHAT_SIDE_PANE["comments"] = None
            return ""
        if key in ("\x04", "\x15"):  # ctrl-d / ctrl-u: scroll the issue or report beside the thread
            CHAT_SIDE_PANE["top"] += 10 if key == "\x04" else -10
            continue
        if key == "\x0e":  # ctrl-n: forget the thread, start fresh
            history, top = [], None
            thread_file.unlink(missing_ok=True)
            continue
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
            top = (top or 0) + {curses.KEY_UP: -1, curses.KEY_DOWN: 1, curses.KEY_PPAGE: -10, curses.KEY_NPAGE: 10}[key]
            continue
        if (typed := text_after_typing(text, key)) is not None:
            text = typed
            continue
        if key not in ("\n", "\r", curses.KEY_ENTER):
            continue
        if not text.strip():
            if draft and history and history[-1]["a"] and choose_from_list(
                    screen, f"{issue['identifier']}  ·  post this comment?", ["yes, post it", "no, back to the draft"]) == 0:
                run_with_spinner(screen, "posting your comment…", lambda: post_comment_to_linear(issue, history[-1]["a"]))
                return f"commented on {issue['identifier']}"
            continue
        question, text = text.strip(), ""
        message = {"q": question, "a": None, "steps": [], "t": datetime.now().strftime("%H:%M")}
        history.append(message)
        outcome, running_processes = {}, []

        def work():
            try:
                if "text" not in prompt_context and report:
                    prompt_context["text"] = (f"Col's daily report:\n\n{report}\n\nWhat it was written from:\n{daily_report_context(issues)}"
                                   + (f"\n\nThe topic they're asking about (answer about this one):\n{topic[1]}" if topic else ""))
                    prompt_context["cwd"] = Path.home()
                elif "text" not in prompt_context:
                    prompt_context["text"] = issue_as_prompt_text(issue)
                    # the issue's worktree, so git/gh see the right repo and branch
                    prompt_context["cwd"] = next(iter(worktrees_on_branch(issue["branchName"]).values()), Path.home())
                questions_and_answers_so_far = [(earlier_message["q"], earlier_message["a"]) for earlier_message in history]
                outcome["a"] = ask_headless_claude(prompt_context["text"], questions_and_answers_so_far, prompt_context["cwd"],
                                                   message["steps"].append, running_processes,
                                                   ASK_ABOUT_REPORT_TASK if report else DRAFT_COMMENT_TASK if draft else ASK_ABOUT_ISSUE_TASK)
            except Exception as e:
                outcome["a"] = f"failed: {e}"
        threading.Thread(target=work, daemon=True).start()
        spinner_frame, UiState.col_is_working, UiState.lookups_col_is_working_on = 0, True, message["steps"]
        while "a" not in outcome:
            draw_chat(screen, title, issue, history, SPINNER_FRAMES[spinner_frame % len(SPINNER_FRAMES)], text, None, draft, report)
            spinner_frame += 1
            key_while_thinking = screen.getch()
            if key_while_thinking == 27:
                for process in running_processes:
                    process.kill()
                outcome["a"] = "_stopped_"
            elif key_while_thinking in (4, 21):  # ctrl-d / ctrl-u: scroll the side pane meanwhile
                CHAT_SIDE_PANE["top"] += 10 if key_while_thinking == 4 else -10
            elif (typed := text_after_typing(text, key_while_thinking)) is not None:  # keep typing while it thinks
                text = typed
        message["a"], message["t2"], top, UiState.col_is_working = outcome["a"], datetime.now().strftime("%H:%M"), None, False
        thread_file.parent.mkdir(parents=True, exist_ok=True)
        thread_file.write_text(json.dumps(history))


def write_or_draft_comment(screen, issue):
    """m: write a comment yourself, or brief Claude and post its draft. Returns a footer message."""
    picked = choose_from_list(screen, f"{issue['identifier']}  ·  comment", ["✎  write it myself", "✦  Col drafts it from a brief"], help=(
        "The comment is posted to the Linear issue as you. Col's draft can take feedback until it's right; "
        "nothing is posted until you confirm.",))
    if picked is None:
        return ""
    if picked:
        return chat_with_col(screen, issue, draft=True)
    comment_text = ask_for_one_line(screen, f"{issue['identifier']}  ·  comment", "enter post · esc cancel", ("one line, Markdown works",))
    if not comment_text:
        return ""
    run_with_spinner(screen, "posting your comment…", lambda: post_comment_to_linear(issue, comment_text))
    return f"commented on {issue['identifier']}"


def choose_from_list(screen, title, items, cursor=0, multi=False, help=(), cards=(), keys="", top_left_corner=None):
    """Centred rounded dialog over the list. j/k move, / filter, l/enter pick, h/esc back.
    Returns the index into items, or None. multi: space marks several, returns the marked indices in the
    order marked (or [highlighted] if none marked), or None. help: paragraphs explaining the choice, shown
    above the items (one line each, wrapped). cards: [(title, text)] shown instead as boxes side by side,
    for choices that are easier to compare than to read. keys: a short key per item ("lca"), pressing one picks
    that item straight away; l still picks the highlighted one, so give l to the first."""
    filter_text, is_typing_filter, marked_item_numbers = "", False, []
    if multi:
        title += "  ·  space: several"
    while True:
        matching_item_numbers = [item_number for item_number, item_text in enumerate(items) if filter_text.lower() in item_text.lower()]
        cursor = min(cursor, max(len(matching_item_numbers) - 1, 0))
        box_width = max(56, max(map(len, items)) + 10, 80 if help else 0, 100 if cards else 0)
        box_width = min(box_width, screen.getmaxyx()[1] - 4)
        help_lines = [wrapped_line for paragraph in help for wrapped_line in textwrap.wrap(paragraph, box_width - 6)] + ([[]] if help else [])
        card_width = (box_width - 7) // max(len(cards), 1)  # each card's width
        card_lines = [textwrap.wrap(text, card_width - 4) for _, text in cards]
        card_height = max((len(card_text_lines) for card_text_lines in card_lines), default=0) + 3
        help_lines = ([[]] * (card_height + 1)) if cards else help_lines  # room for the cards
        rows_for_items = max(min(len(items), screen.getmaxyx()[0] - 10 - len(help_lines)), 1)
        line, box_width = draw_dialog_box(screen, title, rows_for_items + 6 + len(help_lines), box_width, top_left_corner)
        if is_typing_filter or filter_text:
            line(2, f"/ {filter_text}{'▏' if is_typing_filter else ''}", curses.color_pair(ACCENT), 3)
        else:
            line(2, "/ filter", curses.color_pair(DIM), 3)
        for row_offset, text in enumerate(help_lines):
            line(3 + row_offset, text or "", curses.color_pair(MUTED), 3)
        for card_number, ((card_title, _), card_text_lines) in enumerate(zip(cards, card_lines)):  # the cards: a box each, title on top
            card_x, border_colour = 3 + card_number * (card_width + 1), curses.color_pair(BORDER)
            line(3, "╭" + "─" * (card_width - 2) + "╮", border_colour, card_x)
            for row_offset in range(1, card_height - 1):
                line(3 + row_offset, "│" + " " * (card_width - 2) + "│", border_colour, card_x)
            line(3 + card_height - 1, "╰" + "─" * (card_width - 2) + "╯", border_colour, card_x)
            line(3, f" {card_title} ", curses.color_pair(ACCENT) | curses.A_BOLD, card_x + 2)
            for row_offset, card_line in enumerate(card_text_lines):
                line(5 + row_offset, card_line, curses.color_pair(DEFAULT_TEXT), card_x + 2)
        first_item_row = 4 + len(help_lines)
        first_row_shown = max(0, cursor - rows_for_items + 1)
        for row_offset, item_number in enumerate(matching_item_numbers[first_row_shown:first_row_shown + rows_for_items]):
            text = f"{'●' if item_number in marked_item_numbers else '○'} {items[item_number]}" if multi else items[item_number]
            if not is_typing_filter:  # click a row: go there; click the cursor's row: pick it. multi: a click marks
                is_cursor_row = first_row_shown + row_offset == cursor
                register_clickable_row(line.first_row_on_screen + first_item_row + row_offset, 0, screen.getmaxyx()[1],
                                       first_row_shown + row_offset - cursor, [32] if multi else [10] * is_cursor_row)
            if first_row_shown + row_offset == cursor:
                line(first_item_row + row_offset, f" {text}".ljust(box_width - 4), curses.color_pair(SELECTED_ROW) | curses.A_BOLD, 2)
                line(first_item_row + row_offset, "▎", curses.color_pair(SELECTED_ROW_MARKER), 2)
            else:
                line(first_item_row + row_offset, text, curses.color_pair(ACCENT) if item_number in marked_item_numbers else 0, 3)
        if not matching_item_numbers:
            line(first_item_row, "no match", curses.color_pair(MUTED), 3)
        draw_footer_from_text(screen, ("space mark · l/enter done" if multi else "l/enter pick") + (" · or press its key" if keys else "")
                + " · j/k move · type to filter · esc/h back")
        screen.refresh()
        key = screen.getch()
        if key == -1:
            continue
        if is_typing_filter:
            if key == 27:
                filter_text, is_typing_filter = "", False
                col_reacts_to("unfilter")
            elif key in (10, 13, curses.KEY_ENTER):
                is_typing_filter = False
            elif (typed := text_after_typing(filter_text, key)) is not None:
                filter_text, cursor = typed, cursor if len(typed) < len(filter_text) else 0  # a new letter: back to the first match
        elif key in (27, ord("h")):
            if not filter_text:
                return None
            filter_text = ""
        elif key in (10, 13, curses.KEY_ENTER, ord("l")):
            if multi:
                return marked_item_numbers or matching_item_numbers[cursor:cursor + 1] or None
            if matching_item_numbers:
                return matching_item_numbers[cursor]
        elif key == ord(" ") and multi and matching_item_numbers:
            item_number = matching_item_numbers[cursor]
            marked_item_numbers.remove(item_number) if item_number in marked_item_numbers else marked_item_numbers.append(item_number)
        elif key == ord("/"):
            is_typing_filter, filter_text, cursor = True, "", 0
            col_reacts_to("filter")
        elif key in (ord("j"), curses.KEY_DOWN):
            cursor = min(cursor + 1, len(matching_item_numbers) - 1)
        elif key in (ord("k"), curses.KEY_UP):
            cursor = max(cursor - 1, 0)
        elif 32 <= key < 127 and chr(key) in keys:
            return keys.index(chr(key))


def choose_repositories_and_make_worktrees(screen, issue, repositories_already_set_up):
    """Repo picker (several allowed) -> a worktree on the issue's branch in each.
    Returns {repo: path}, {} on cancel, None for "no repo" (only offered before the issue has any repo)."""
    choices = ([] if repositories_already_set_up else [NO_REPOSITORY_CHOICE]) + [repository for repository in local_repository_names() if repository not in repositories_already_set_up]
    picked = choose_from_list(screen, f"{issue['identifier']}  ·  repo", choices, multi=True, cards=(
        ("⎇  in a repo", "Where does the change live? Each repo you pick gets its own worktree, a separate checkout next to "
                         f"the repo as <repo>-wt/<branch>, on the issue's branch {issue['branchName']}. Your main checkout is "
                         "untouched. Col starts in the first one and can reach the others."),
        ("∅  no repo", "Col with just the issue, in its notes folder: for thinking, writing the issue, or research. "
                       "You can add a repo later from the phase list.")))
    if not picked:
        return {}
    if not repositories_already_set_up and 0 in picked:
        return None
    for item_number in reversed(picked):  # first picked ends up most recent
        remember_as_most_recently_used("repos", choices[item_number], local_repository_names())
    return run_with_spinner(screen, f"setting up {issue['branchName']} in {', '.join(choices[item_number] for item_number in picked)}",
                lambda: {choices[item_number]: ensure_worktree_for_branch(REPOSITORIES_FOLDER / choices[item_number], issue["branchName"]) for item_number in picked})


def fetch_comments(issue):
    """[(who · when, body)] for the issue, oldest first."""
    nodes = linear_graphql("query($id: String!) { issue(id: $id) { comments(first: 100, orderBy: createdAt) { nodes { body createdAt "
                "user { displayName } } } } }", {"id": issue["id"]})["issue"]["comments"]["nodes"]
    return [(f"{(c['user'] or {}).get('displayName', '?')}  ·  {c['createdAt'][:10]}", c["body"] or "")
            for c in sorted(nodes, key=lambda c: c["createdAt"])]


def show_issue_preview(screen, issue, open_workspace_summary=False):
    """The issue inline: a Linear-ish header line (state, priority, assignee, PRs) then the description rendered
    from Markdown. l opens the comments in a pane on the right and focuses it; j/k scroll the focused pane,
    h goes back to the description and then closes. enter starts the issue (returns "launch"), esc/space close."""
    first_line_shown_by_pane, focused_pane, comments = [0, 0], 0, None  # notes: None until l; "loading" while the fetch runs; then the list
    fetched, frame_count, first_line_of_each_comment = {}, 0, []
    comment_being_written = None  # C: a comment being written in a box under the comments; None when not composing

    def load():
        try:
            fetched["done"] = fetch_comments(issue)
        except Exception as error:
            fetched["done"] = [("couldn't load comments", str(error))]
    refreshed_comments_waiting = []  # a refreshed thread arriving after a post
    while True:
        if comments == "loading" and "done" in fetched:
            comments = fetched["done"]
        if refreshed_comments_waiting:
            comments, first_line_shown_by_pane[1] = refreshed_comments_waiting.pop(), 10 ** 6
        frame_count += 1
        screen_height, screen_width = screen.getmaxyx()
        box_height, box_width = screen_height - 10, min(screen_width - 6, 110 if comments is None else 150)
        inner_width = box_width - 6
        icon, icon_colour = progress_icon_for_state(issue["state"]["name"], issue["state"]["type"])
        bars, word, priority_colour = PRIORITY_DISPLAY.get(issue["priority"], ("▯▯▯", "no priority", DIM))
        header_parts = [(f"{icon} {issue['state']['name']}", icon_colour), (f"{bars} {word}", priority_colour),
                (f"@ {(issue.get('assignee') or {}).get('displayName', 'unassigned')}", MUTED)]
        badge = pull_request_badge(issue)
        if badge[0]:
            header_parts.append(badge)
        if open_workspace_summary:
            header_parts.append((f"▶ {open_workspace_summary} · w jumps there", GREEN))  # here: "ws 4 · working · repo · branch"
        line, box_width = draw_dialog_box(screen, f"{issue['identifier']}  ·  {issue['title'][:box_width - 24]}", box_height, box_width)
        # panes: [(x, width, lines)]; the comments pane takes the right 45% once open
        description_width = inner_width if comments is None else inner_width * 55 // 100 - 2
        panes = [(3, description_width, markdown_to_styled_lines(issue.get("description") or "_no description yet_", description_width))]
        if comments is not None:
            comments_x, comments_width = 3 + description_width + 3, inner_width - description_width - 3
            comment_lines = []
            if comments == "loading":  # the pane opens at once; a spinner and a shimmering skeleton while comments arrive
                comment_lines.append([(f"{SPINNER_FRAMES[frame_count % len(SPINNER_FRAMES)]} loading comments…", "head")])
                for placeholder_number in range(4):
                    comment_lines += [[], [("▆" * (comments_width * (3 + (placeholder_number + frame_count // 2) % 3) // 6), "dim")],
                                      [("▆" * (comments_width * (2 + (placeholder_number + frame_count // 2) % 4) // 6), "dim")]]
            else:
                first_line_of_each_comment = []  # first line of each comment: j/k in the pane jump between them
                for author, text in comments:
                    first_line_of_each_comment.append(len(comment_lines))
                    comment_lines += [[(author, "head")]] + markdown_to_styled_lines(text, comments_width) + [[]]
            panes.append((comments_x, comments_width, comment_lines or [[("no comments yet", "mut")]]))
            for row_offset in range(2, box_height - 2):
                line(row_offset, "│", curses.color_pair(BORDER), comments_x - 2)
        column = 3
        for text, colour in header_parts:
            line(2, text, curses.color_pair(colour) | curses.A_BOLD, column)
            column += len(text) + 3
        line(3, "─" * description_width, curses.color_pair(BORDER), 3)
        body = box_height - 6
        attribute_by_style = dict(TEXT_ATTRIBUTE_BY_STYLE, code=curses.color_pair(CODE), head=curses.color_pair(ACCENT) | curses.A_BOLD,
                     mut=curses.color_pair(MUTED), dim=curses.color_pair(DIM))
        for pane_number, (pane_x, pane_width, pane_lines) in enumerate(panes):
            if pane_number == 1:
                line(2, "comments" + (f"  {len(comments)}" if comments != "loading" else ""), curses.color_pair(ACCENT if focused_pane else MUTED) | curses.A_BOLD, pane_x)
                line(3, "─" * pane_width, curses.color_pair(BORDER), pane_x)
            rows_for_pane = body - 4 if pane_number == 1 and comment_being_written is not None else body  # the composer takes the bottom of the pane
            first_line_shown_by_pane[pane_number] = max(0, min(first_line_shown_by_pane[pane_number], len(pane_lines) - rows_for_pane))
            for row_offset, segments in enumerate(pane_lines[first_line_shown_by_pane[pane_number]:first_line_shown_by_pane[pane_number] + rows_for_pane]):
                column = pane_x
                for text, style in segments:
                    if column < pane_x + pane_width:
                        line(4 + row_offset, text[:pane_x + pane_width - column], attribute_by_style[style], column)
                    column += len(text)
            if len(pane_lines) > rows_for_pane:
                position_text = f" {first_line_shown_by_pane[pane_number] + rows_for_pane}/{len(pane_lines)} "
                line(box_height - 2, position_text, curses.color_pair(ACCENT if focused_pane == pane_number else DIM), pane_x + pane_width - len(position_text))
            if pane_number == 1 and comment_being_written is not None:  # the composer: a box under the comments, posted as you
                composer_border_colour = curses.color_pair(COL_ACCENT)
                line(box_height - 6, "╭" + "─" * (pane_width - 2) + "╮", composer_border_colour, pane_x)
                line(box_height - 5, "│" + " " * (pane_width - 2) + "│", composer_border_colour, pane_x)
                line(box_height - 4, "╰" + "─" * (pane_width - 2) + "╯", composer_border_colour, pane_x)
                line(box_height - 4, " enter post · esc cancel ", curses.color_pair(DIM), pane_x + pane_width - 27)
                line(box_height - 5, "›", curses.color_pair(COL_ACCENT) | curses.A_BOLD, pane_x + 2)
                line(box_height - 5, f"{comment_being_written}▏"[-(pane_width - 6):] if comment_being_written else "", curses.A_BOLD, pane_x + 4)
                if not comment_being_written:
                    line(box_height - 5, "write a comment…", curses.color_pair(DIM), pane_x + 5)
        footer_text = ("j/k next/previous comment · d/u half page · C write a comment · c Col drafts one · a ask Col · h back · esc close" if focused_pane else
                ("w jump to workspace · " if open_workspace_summary else "enter start · ") +
                "l comments · a ask Col · c Col drafts a comment · C write a comment · L open in Linear · j/k scroll · d/u half page · esc/h close")
        draw_footer_from_text(screen, footer_text)
        screen.refresh()
        if comments == "loading":
            screen.timeout(80)  # animate the skeleton
        if comment_being_written is not None:  # composing: everything typed goes into the comment
            try:
                typed_character = screen.get_wch()  # wide chars, so å/ä/ö work
            except curses.error:
                continue
            finally:
                screen.timeout(250)
            if typed_character == "\x1b":
                comment_being_written = None
            elif typed_character in ("\n", "\r", curses.KEY_ENTER):
                if comment_being_written.strip():
                    run_with_spinner(screen, "posting your comment…", lambda body=comment_being_written.strip(): post_comment_to_linear(issue, body))
                    COL_SPEECH.update(mood="cheer", say=COL_REACTIONS["comment"][int(time.time()) % len(COL_REACTIONS["comment"])], until=time.time() + 10)
                    # show it at once (Linear can lag a moment), then refresh the thread behind the scenes
                    fetched.pop("done", None)
                    comments = (comments if isinstance(comments, list) else []) + [(f"you  ·  {datetime.now():%Y-%m-%d}", comment_being_written.strip())]
                    first_line_shown_by_pane[1], comment_being_written = 10 ** 6, None
                    def refresh_notes():
                        load()
                        if "done" in fetched and isinstance(fetched["done"], list):
                            refreshed_comments_waiting.append(fetched["done"])
                    threading.Thread(target=refresh_notes, daemon=True).start()
            elif (typed := text_after_typing(comment_being_written, typed_character)) is not None:
                comment_being_written = typed
            continue
        key = screen.getch()
        screen.timeout(250)
        if key == -1:
            continue
        if key in (27, ord(" "), ord("q")):
            return None
        if key == ord("C"):  # write a comment here, with the thread in view
            if comments is None:
                comments = "loading"
                threading.Thread(target=load, daemon=True).start()
            focused_pane, comment_being_written = 1, ""
            continue
        if key == ord("c"):  # Col drafts a comment from a brief, the thread beside it; the pane reloads once it's posted
            CHAT_SIDE_PANE["comments"], CHAT_SIDE_PANE["top"] = (comments if isinstance(comments, list) else None), None  # None: open at the comments
            if chat_with_col(screen, issue, draft=True).startswith("commented"):
                COL_SPEECH.update(mood="cheer", say=COL_REACTIONS["comment"][int(time.time()) % len(COL_REACTIONS["comment"])], until=time.time() + 10)
                comments, focused_pane, first_line_shown_by_pane[1] = "loading", 1, 10 ** 6
                fetched.pop("done", None)
                threading.Thread(target=load, daemon=True).start()
            continue
        if key == ord("h"):
            if not focused_pane:
                return None
            focused_pane = 0
        elif key == ord("l"):
            if comments is None:  # expand now, fetch in the background
                comments = "loading"
                threading.Thread(target=load, daemon=True).start()
            focused_pane = 1
        elif key in (10, 13, curses.KEY_ENTER):
            return "launch"
        elif key in (ord("a"), ord("L")) or (key == ord("w") and open_workspace_summary):
            if key == ord("a"):  # the chat shows the comments beside the issue when they were open here
                CHAT_SIDE_PANE["comments"] = comments if isinstance(comments, list) else None
            return chr(key)
        if focused_pane and first_line_of_each_comment and key in (ord("j"), curses.KEY_DOWN, ord("k"), curses.KEY_UP):  # comment to comment
            going_down, shown_from = key in (ord("j"), curses.KEY_DOWN), first_line_shown_by_pane[1]
            comment_starts_in_that_direction = [first_line for first_line in first_line_of_each_comment
                                                if (first_line > shown_from if going_down else first_line < shown_from)]
            if comment_starts_in_that_direction:
                first_line_shown_by_pane[1] = min(comment_starts_in_that_direction) if going_down else max(comment_starts_in_that_direction)
        else:
            first_line_shown_by_pane[focused_pane] += {ord("j"): 1, curses.KEY_DOWN: 1, ord("k"): -1, curses.KEY_UP: -1,
                            ord("d"): body // 2, ord("u"): -body // 2}.get(key, 0)


def start_work_on_issue(screen, issue, phase=None):
    """Repos (unless the issue already has worktrees) -> phase (unless given) -> claude in a new tab, started in
    the first worktree with access to the others. Exits the popup, or returns an error."""
    identifier = issue["identifier"]
    workspace = workspace_open_for_issue(issue)
    pull_request_branches = linked_pull_request_branches(issue) if phase is None else []
    if phase is None:  # how to start: phases, or a clean slate in a repo / without one / on a linked PR's branch
        open_workspace_count = len(workspaces_open_for_issue(issue))
        jump_row = "▶  jump to its open workspace  ·  you're already working on it" if open_workspace_count == 1 else \
            f"▶  jump to one of its {open_workspace_count} open workspaces  ·  you're already working on it"
        ways_to_start = ([jump_row] if workspace else []) + \
                ["→  phases on the issue branch", "⎇  clean slate in a repo  ·  pick one, a worktree on the issue branch",
                 "○  clean slate without a repo  ·  just the issue, for thinking and writing"] + \
                [f"⎇  clean slate on PR #{n}  ·  {repo}  {branch}" for repo, branch, n in pull_request_branches]
        n = choose_from_list(screen, f"{identifier}  ·  start", ways_to_start, cards=(
            ("→  phases", "Col walks you through the issue step by step: write → plan → build → review. "
                          "Runs in a worktree on the issue's own branch. Notes are kept between sessions, so you can stop and pick up later."),
            ("⎇  clean slate", "Col with just the issue, no steps. In a repo (a worktree on the issue's branch), without one "
                               "(just the notes folder), or on a linked PR's branch. For a quick look, a review, or a small fix.")))
        if n is None:
            return ""
        if workspace and n == 0:
            return jump_to_a_workspace_of_issue(screen, issue)
        n -= bool(workspace)
        if n == 1:  # clean slate in a repo
            worktree_by_repository = choose_repositories_and_make_worktrees(screen, issue, [])
            if not worktree_by_repository:
                return "" if worktree_by_repository is not None else start_clean_slate(screen, issue)
            return start_clean_slate(screen, issue, cwd=list(worktree_by_repository.values())[0], extra=list(worktree_by_repository.values())[1:])
        if n == 2:
            return start_clean_slate(screen, issue)
        if n > 2:
            return start_clean_slate_on_pull_request(screen, issue, *pull_request_branches[n - 3])
    worktree_by_repository = run_with_spinner(screen, "looking for the issue's worktrees…", lambda: worktrees_on_branch(issue["branchName"]))
    if not worktree_by_repository:
        worktree_by_repository = choose_repositories_and_make_worktrees(screen, issue, [])
        if worktree_by_repository is None:  # no repo: a new issue still gets written, anything else is a clean slate
            if phase != "issue":
                return start_clean_slate(screen, issue)
            worktree_by_repository = {}
        elif not worktree_by_repository:
            return ""
    notes_folder = NOTES_DIR / identifier
    # an issue someone already wrote a description for counts as written
    phase_is_done = [(notes_folder / f"{phase_name}.md").exists() or (phase_name == "issue" and bool((issue.get("description") or "").strip()))
            for phase_name, _ in PHASES]
    # ✓ notes written, ◐ started (launching writes prompt-<phase>.md), · not started
    phase_marks = ["✓" if is_done else "◐" if (notes_folder / f"prompt-{phase_name}.md").exists() else "·" for (phase_name, _), is_done in zip(PHASES, phase_is_done)]
    phase_labels = [f"{mark}  {phase_name:<11}{description}" for (phase_name, description), mark in zip(PHASES, phase_marks)]
    while not phase:
        add_repository_line = f"+  add a repo    (now: {', '.join(worktree_by_repository)})"
        n = choose_from_list(screen, f"{identifier}  ·  phase", phase_labels + [add_repository_line],
                   next((phase_number for phase_number, is_done in enumerate(phase_is_done) if not is_done), len(PHASES) - 1), cards=(
                       ("→  a phase", "One step of the work, with Col in a new tab of this issue's workspace: write the issue, "
                                      "plan it, build it, review it. Each step leaves notes in ~/.claude/work/" + identifier + "/ so the "
                                      "next one, maybe days later, picks up where you stopped. Pick the step you're at."),
                       ("✓ ◐ ·  the marks", "✓ its notes are written, that step is done.  ◐ started, there's a prompt but no notes yet.  "
                                            "· not started.  The cursor lands on the first step that isn't done.")))
        if n is None:
            return ""
        if n == len(PHASES):
            worktree_by_repository |= choose_repositories_and_make_worktrees(screen, issue, list(worktree_by_repository))
        else:
            phase = PHASES[n][0]
    worktree_folders = list(worktree_by_repository.values())
    first_folder = worktree_folders[0] if worktree_folders else notes_folder  # no repo: work from the notes folder
    notes_folder.mkdir(parents=True, exist_ok=True)
    workspace = workspace or next((candidate_workspace for candidate_workspace in open_herdr_workspaces()
                                   if candidate_workspace.get("worktree", {}).get("checkout_path") == first_folder), None)
    prompt_file = notes_folder / f"prompt-{phase}.md"
    prompt_file.write_text(render_phase_prompt(phase, issue, worktree_folders, notes_folder))
    show_status_message(screen, f"starting {phase}…")
    # --add-dir: the notes live outside the worktree, let claude read and write them without asking
    if error := open_claude_in_workspace(issue, workspace, first_folder, phase, prompt_file, [notes_folder] + worktree_folders[1:]):
        return error
    def tell_linear_the_work_has_started():
        assign_to_me_if_unassigned(issue)
        if phase != "issue":  # writing the issue isn't starting the work
            move_issue_to_started(issue)
    run_with_spinner(screen, "telling Linear you're on it…", tell_linear_the_work_has_started)
    show_col_send_off(screen, phase)
    sys.exit()


def focus_workspace_and_close_popup(workspace):
    """Focus the issue's herdr workspace and close the popup."""
    run_command("herdr", "workspace", "focus", workspace["workspace_id"])
    sys.exit()


def open_claude_in_workspace(issue, workspace, cwd, tab_label, prompt_file, extra_folders):
    """New tab in the issue's workspace (or a new workspace) running claude on prompt. Returns an error or None."""
    if workspace:
        run_command("herdr", "workspace", "focus", workspace["workspace_id"])
        created = run_command("herdr", "tab", "create", "--workspace", workspace["workspace_id"], "--cwd", str(cwd), "--label", tab_label, "--focus")
    else:
        created = run_command("herdr", "workspace", "create", "--cwd", str(cwd), "--label", f"{issue['identifier']} {issue['title']}", "--focus")
    if created.returncode:
        return f"herdr failed: {(created.stderr or created.stdout).strip()[-120:]}"
    created_ids = json.loads(created.stdout)["result"]
    if not workspace:  # workspace create names its first tab "1"
        run_command("herdr", "tab", "rename", created_ids["tab"]["tab_id"], tab_label)
    # prompt first: --add-dir takes several dirs and would swallow a prompt placed after it
    quoted_folders = " ".join(shlex.quote(str(folder)) for folder in extra_folders)
    run_command("herdr", "pane", "run", created_ids["root_pane"]["pane_id"], f'claude "$(cat {shlex.quote(str(prompt_file))})" --add-dir {quoted_folders}')


def start_clean_slate_on_pull_request(screen, issue, repo, branch, number):
    """Clean slate in a worktree of a linked PR's branch: Claude with the issue (and the PR), no phases."""
    worktree_folder = run_with_spinner(screen, f"setting up {repo} on {branch}", lambda: ensure_worktree_for_branch(REPOSITORIES_FOLDER / repo, branch))
    notes_folder = NOTES_DIR / issue["identifier"]
    notes_folder.mkdir(parents=True, exist_ok=True)
    prompt_file = notes_folder / f"prompt-pr-{number}.md"
    prompt_file.write_text(render_phase_prompt("clean", issue, [], notes_folder) + f"\nYou're in {worktree_folder}, a worktree of {repo} on {branch}, "
                      f"the branch of PR #{number} linked to the issue. Read the PR too (gh pr view {number}).\n")
    show_status_message(screen, f"starting #{number}…")
    workspace = workspace_open_for_issue(issue)
    if error := open_claude_in_workspace(issue, workspace, worktree_folder, f"#{number}", prompt_file, [notes_folder]):
        return error
    show_col_send_off(screen, "pr")
    sys.exit()


def start_clean_slate(screen, issue, topic=None, cwd=None, extra=()):
    """Claude with just the issue: no phases. Runs in the issue's notes folder, or in cwd (a worktree) with the
    notes and any extra worktrees reachable. topic: a report topic with no issue; Col starts from that text instead."""
    notes_folder = NOTES_DIR / issue["identifier"]
    notes_folder.mkdir(parents=True, exist_ok=True)
    prompt_file = notes_folder / "prompt-clean.md"
    prompt_file.write_text((render_phase_prompt("clean", issue, [], notes_folder) + (f"\nYou're in a worktree of the repo at {cwd}, on the issue's branch."
                                                            + (" Also checked out: " + ", ".join(map(str, extra)) if extra else "") if cwd else ""))
                      if not topic else
                      f"From Col's daily report ({datetime.now():%Y-%m-%d}), a topic with no Linear issue yet:\n\n{topic}\n\n"
                      f"Look up what's behind it (Linear, Slack, GitHub via gh) and tell me in a few lines where it stands, "
                      f"then wait for me. If you want to keep notes, put them in {notes_folder}/.")
    show_status_message(screen, "starting clean slate…")
    workspace = workspace_open_for_issue(issue)
    if error := open_claude_in_workspace(issue, workspace, cwd or notes_folder, "clean", prompt_file, [notes_folder] + list(extra)):
        return error
    run_with_spinner(screen, "telling Linear you're on it…", lambda: assign_to_me_if_unassigned(issue))
    show_col_send_off(screen, "clean")
    sys.exit()


def create_issue_and_write_it(screen, title="", body=""):
    """Team -> title -> issue assigned to me, then straight into the "issue" phase, where claude helps write it.
    title / body: a starting title and description (a topic from the daily report, say)."""
    my_user_id, team = choose_team(screen, "new issue  ·  team")
    if not team:
        return ""
    title = ask_for_one_line(screen, f"new {team['key']} issue  ·  title", text=title)
    if not title:
        return ""
    issue = linear_graphql_with_spinner(
        screen, "creating the issue…", "mutation($t: String!, $d: String, $team: String!, $me: String!) { issueCreate(input: "
        "{title: $t, description: $d, teamId: $team, assigneeId: $me}) { issue { %s } } }" % ISSUE_FIELDS,
        {"t": title, "d": body or None, "team": team["id"], "me": my_user_id})["issueCreate"]["issue"]
    return start_work_on_issue(screen, issue, "issue") or f"created {issue['identifier']}"


class KeyBinding(NamedTuple):
    """One key of the issue list. The key handling, the ? screen, the footer and "which letters start a filter"
    are all read from the table below, so they cannot disagree with each other."""
    keys: str                        # the characters that trigger it: "l\n" is l or enter. "" for a row that only documents
    action: str                      # the IssueListScreen method it runs
    needs_issue_under_cursor: bool   # does nothing while the list is empty
    reference_group: str = ""        # its heading on the ? screen; "" leaves it off that screen
    reference_keys: str = ""         # how the key is written there
    reference_text: str = ""         # a word or two, there
    footer_keys: str = ""            # how the key is written in the footer; "" leaves it out of the footer
    footer_text: str = ""


MOVE_AROUND, WORK_ON_THE_ISSUE, OPEN_ELSEWHERE, CHANGE_THE_ISSUE, VIEWS = \
    "Move around", "Work on the issue", "Open elsewhere", "Change the issue", "Views"
REFERENCE_GROUPS_IN_ORDER = (MOVE_AROUND, WORK_ON_THE_ISSUE, OPEN_ELSEWHERE, CHANGE_THE_ISSUE, VIEWS)
# In footer order. On the ? screen each group lists its keys in this order too.
KEY_BINDINGS = [
    #          keys     action                               issue?  group on ?         keys on ?            text on ?                footer keys, footer text
    KeyBinding("l\n",   "start_work_on_issue_under_cursor",  True,   WORK_ON_THE_ISSUE, "enter  or  l",      "start working on it",   "l/enter", "start"),
    KeyBinding(" ",     "preview_issue_under_cursor",        True,   WORK_ON_THE_ISSUE, "space",             "preview",               "space", "preview"),
    KeyBinding("w",     "jump_to_workspace_of_issue",        True,   WORK_ON_THE_ISSUE, "w",                 "jump to its workspace", "w", "jump to its workspace"),
    KeyBinding("a",     "ask_col_about_issue",               True,   WORK_ON_THE_ISSUE, "a",                 "ask Col",               "a", "ask Col"),
    KeyBinding("C",     "comment_on_issue",                  True,   WORK_ON_THE_ISSUE, "C",                 "comment",               "C", "comment"),
    KeyBinding("c",     "start_clean_slate_on_issue",        True,   WORK_ON_THE_ISSUE, "c",                 "clean slate with Col",  "c", "clean slate"),
    KeyBinding("S",     "change_status_of_issue",            True,   CHANGE_THE_ISSUE,  "S",                 "status",                "S", "status"),
    KeyBinding("P",     "change_priority_of_issue",          True,   CHANGE_THE_ISSUE,  "P",                 "priority",              "P", "priority"),
    KeyBinding("A",     "change_assignee_of_issue",          True,   CHANGE_THE_ISSUE,  "A",                 "assign",                "A", "assign"),
    KeyBinding("X",     "cancel_issue_under_cursor",         True,   CHANGE_THE_ISSUE,  "X",                 "cancel",                "X", "cancel"),
    KeyBinding("U",     "undo_last_change",                  False,  CHANGE_THE_ISSUE,  "U",                 "undo last change",      "U", "undo"),
    KeyBinding("n",     "create_new_issue",                  False,  CHANGE_THE_ISSUE,  "n",                 "new issue",             "n", "new issue"),
    KeyBinding("b",     "open_daily_report",                 False,  VIEWS,             "b",                 "daily report",          "b", "daily report"),
    KeyBinding("t",     "browse_a_team",                     False,  VIEWS,             "t",                 "team",                  "t/T", "team / project"),
    KeyBinding("T",     "browse_a_project",                  False,  VIEWS,             "T",                 "project"),
    KeyBinding("R",     "open_review_inbox",                 False,  VIEWS,             "R",                 "review inbox",          "R", "reviews"),
    KeyBinding("r",     "refresh_issues_now",                False,  VIEWS,             "r",                 "refresh",               "r", "refresh"),
    KeyBinding("g",     "open_pull_request_of_issue",        True,   OPEN_ELSEWHERE,    "g",                 "pull request",          "g", "PR"),
    KeyBinding("L",     "open_issue_in_linear_app",          True,   OPEN_ELSEWHERE,    "L",                 "Linear app",            "L", "Linear"),
    KeyBinding("y",     "copy_link_of_issue",                True,   OPEN_ELSEWHERE,    "y",                 "copy link",             "y", "copy link"),
    KeyBinding("j",     "move_cursor_down",                  True,   MOVE_AROUND,       "j / k  or  ↓ / ↑",  "next / previous",       "j/k", "move"),
    KeyBinding("k",     "move_cursor_up",                    True),
    KeyBinding("",      "",                                  False,  MOVE_AROUND,       "5j  /  5k",         "jump 5 rows"),
    KeyBinding("G",     "move_cursor_to_last_issue",         True,   MOVE_AROUND,       "G",                 "last issue"),
    KeyBinding("/",     "start_typing_a_filter",             False,  MOVE_AROUND,       "type, or /",        "filter · esc clears",   "/", "filter"),
    KeyBinding("",      "",                                  False,  MOVE_AROUND,       "click / wheel",     "rows, hints · scroll"),
    KeyBinding("?",     "show_every_key",                    False,  "",                "",                  "",                      "?", "all keys"),
    KeyBinding("h\x1b", "go_back_or_close",                  False,  VIEWS,             "h  or  esc",        "back, then close",      "h", "close"),
    KeyBinding("q",     "close_popup",                       False,  VIEWS,             "q",                 "close"),
    KeyBinding("Z",     "toggle_col",                        False,  VIEWS,             "Z",                 "Col on / off"),
    KeyBinding("K",     "toggle_footer_hints",               False,  VIEWS,             "K",                 "footer hints on / off"),
    KeyBinding("D",     "open_setup",                        False,  VIEWS,             "D",                 "setup"),
    KeyBinding("B",     "give_col_a_bone",                   False,  VIEWS,             "B",                 "give Col a bone"),
    KeyBinding("O",     "show_the_tour_again",               False,  VIEWS,             "O",                 "the quick tour"),
]
KEY_BINDING_BY_CHARACTER = {character: binding for binding in KEY_BINDINGS for character in binding.keys}
# ? : a word or two per key. What a key does inside a preview, chat or report is in that screen's own footer.
# Two columns: the first three groups left, the rest right.
KEY_REFERENCE_GROUPS = [(group, [(binding.reference_keys, binding.reference_text) for binding in KEY_BINDINGS if binding.reference_group == group])
                        for group in REFERENCE_GROUPS_IN_ORDER]
# Any other printable character typed on the list starts a filter with it. Digits are a count (5j), so they are bound too.
CHARACTERS_BOUND_ON_THE_LIST = set(KEY_BINDING_BY_CHARACTER) | set("0123456789")
KEYCODE_ALIASES = {13: 10, curses.KEY_ENTER: 10, curses.KEY_DOWN: ord("j"), curses.KEY_UP: ord("k")}  # other ways to press a bound key


def scan_worktrees_of_all_repositories():
    """{branch: {repository name: folder}} for everything checked out under the repositories folder (worktrees and main
    checkouts), most recently used repository first. One git call per repository, so run it off the main thread."""
    folder_by_branch_and_repository = {}
    for repository in local_repository_names():
        listing = run_command("git", "-C", str(REPOSITORIES_FOLDER / repository), "worktree", "list", "--porcelain").stdout
        for block in listing.split("\n\n"):
            folder = re.search(r"^worktree (.+)$", block, re.M)
            branch = re.search(r"^branch refs/heads/(.+)$", block, re.M)
            if folder and branch:
                folder_by_branch_and_repository.setdefault(branch.group(1), {})[repository] = folder.group(1)
    return folder_by_branch_and_repository


def days_since_last_commit_in(folder):
    """Whole days since the last commit on the branch checked out in folder, or None if git can't say."""
    committed_at = run_command("git", "-C", folder, "log", "-1", "--format=%ct").stdout.strip()
    return int((time.time() - int(committed_at)) // 86400) if committed_at.isdigit() else None


class IssueListScreen:
    """The main screen: the list of issues, Col in the corner, the footer.
    State lives in the attributes, draw() paints it, and each key runs the method KEY_BINDINGS names for it."""

    FIRST_LIST_ROW = 4           # row 0: the title. row 2: the views. row 3: a rule. the list starts here
    ROWS_BELOW_THE_LIST = 5      # Col's strip (4 rows) and the footer row

    def __init__(self, screen):
        self.screen = screen
        self.today = datetime.now().strftime("%Y-%m-%d")
        # what is listed
        self.view = None                         # None: my issues, else the team/project being browsed ({"kind", "id", "name", …})
        self.issues = None                       # as Linear returned them for the view; None until the first load
        self.all_rows = []                       # ("head" | "issue" | "note" | "blank", value) for every issue of the view
        self.visible_rows = []                   # all_rows that match the filter
        self.issue_row_numbers = []              # which of visible_rows are issues: the rows the cursor can stand on
        self.cursor = 0                          # index into issue_row_numbers
        self.identifier_to_put_cursor_on = None  # after a refresh or a change the cursor follows the issue, not the row number
        self.identifier_last_under_cursor = None
        self.cursor_came_to_rest_at = time.time()  # Col falls asleep when it has rested for three minutes
        self.show_bark_for_a_new_list = True
        # the filter, the count and the message in the footer
        self.filter_text = ""
        self.is_typing_filter = False
        self.count_typed_before_a_key = ""       # the 5 of 5j
        self.repeat_count = 1                    # that count as a number, for the key being handled
        self.footer_message = ""
        self.popup_should_close = False
        self.onboarding_tour_is_due = False      # the first time the list opens, the tour shows by itself
        # fetched in the background
        self.refresh_is_running = False
        self.refreshed_issues_waiting = None     # (view they were fetched for, issues) until the main loop takes them in
        self.refresh_error = ""
        self.states_before_first_refresh = {}    # what the morning digest compares the fresh issues with
        self.open_workspaces_by_identifier = {}  # issue id -> the herdr workspaces open for it (usually one)
        self.worktree_folders_by_branch = {}     # branch -> {repository name: folder}, for everything under the repositories folder
        self.days_quiet_by_identifier = {}       # issue id -> days since the last commit on its branch
        # the header
        self.done_today_count = 0
        self.header_flash_until = 0              # the view tag flashes green for a moment when an issue is finished

    # ── start-up and the main loop ────────────────────────────────────────────────────────────────────────────────────

    def run(self):
        """Show the list until the popup closes."""
        global DEMO_SETUP_MODE
        self.start_up()
        while not self.popup_should_close:
            self.take_in_what_the_background_fetched()
            self.work_out_visible_rows_and_cursor()
            if self.onboarding_tour_is_due:
                self.onboarding_tour_is_due = False
                write_stored_value("tour-seen", True)
                start_guided_tour(self.screen)
            if UiState.tour_closing_card_is_due:
                UiState.tour_closing_card_is_due = False
                show_tour_card(self.screen, TOUR_CLOSING_CARD, "l/enter done")
                col_says("off you go. ? if you forget a key, O for the tour", "cheer", 8)
            self.draw()
            col_is_animating = (COL_SPEECH["mood"] == "woof" and time.time() < COL_SPEECH["until"]) or time.time() % 37 < 2.5 \
                or UiState.col_is_working
            self.screen.timeout(120 if col_is_animating else 250)
            key = self.screen.getch()
            if DEMO_SETUP_MODE and self.issue_row_numbers:  # --demo-setup: open the onboarding screen over the loaded list, once
                key, DEMO_SETUP_MODE = ord("D"), False
            if key != -1:
                self.handle_key(key)

    def start_up(self):
        setup_was_just_completed = (not SETUP_PASSED_FILE.exists() or not saved_user_name() or not linear_api_key_in_keychain()) \
            and run_setup(self.screen)
        self.issues = self.issues_from_cache(None)
        self.states_before_first_refresh = snapshot_of_states_and_badges(self.issues or [])
        self.all_rows = build_issue_rows(self.issues) if self.issues is not None else [("note", "loading issues…")]
        self.refresh_issues_in_background()
        if last_view := read_stored_value("last-view"):  # the team/project you used last: warm its cache so switching is instant
            threading.Thread(target=lambda: fetch_issues(last_view), daemon=True).start()
        self.identifier_to_put_cursor_on = (read_stored_value("last") or {}).get("sel")  # resume on the issue you last touched
        UiState.col_is_visible = (read_stored_value("col") or {}).get("on", True)
        UiState.footer_hints_visible = (read_stored_value("hints") or {}).get("on", True)
        if (saved_report := read_stored_value("brief") or {}).get("date") == self.today:
            DAILY_REPORT.update(saved_report)
        if setup_was_just_completed:
            col_says("all set. let's find you an issue", "cheer", 6)
        done = read_stored_value("done") or {}
        self.done_today_count = done.get("n", 0) if done.get("date") == self.today else 0
        UiState.draw_screen_behind_dialog = self.draw
        self.onboarding_tour_is_due = not read_stored_value("tour-seen")
        threading.Thread(target=self.scan_workspaces_and_worktrees, args=(self.issues or [],), daemon=True).start()

    def issues_from_cache(self, view):
        try:
            return json.loads(cache_file_for_view(view).read_text())
        except (OSError, ValueError):
            return None

    def refresh_issues_in_background(self):
        if self.refresh_is_running:
            return
        self.refresh_is_running = True
        view_being_fetched = self.view

        def fetch_and_hand_over():
            try:
                self.refreshed_issues_waiting = (view_being_fetched, fetch_issues(view_being_fetched))
            except Exception as error:  # no key, offline…
                self.refresh_error = f"refresh failed: {error}"[:160]
            self.refresh_is_running = False
        threading.Thread(target=fetch_and_hand_over, daemon=True).start()

    def scan_workspaces_and_worktrees(self, my_issues):
        """Background: which issues have a herdr workspace open, what is checked out where, and for my started
        issues how long their branch has been quiet. Best effort: the list works without any of it."""
        try:
            workspaces_by_identifier = {}
            for workspace in open_herdr_workspaces():
                workspaces_by_identifier.setdefault(workspace["label"].split(" ")[0], []).append(workspace)
            self.open_workspaces_by_identifier = workspaces_by_identifier
            self.worktree_folders_by_branch = scan_worktrees_of_all_repositories()
            days_quiet = {}
            for issue in my_issues:
                folders = self.worktree_folders_by_branch.get(issue.get("branchName"))
                if issue["state"]["type"] == "started" and folders:
                    days = days_since_last_commit_in(next(iter(folders.values())))
                    if days is not None:
                        days_quiet[issue["identifier"]] = days
            self.days_quiet_by_identifier = days_quiet
        except Exception:
            pass

    def switch_to_view(self, view):
        self.view, self.filter_text, self.cursor, self.show_bark_for_a_new_list = view, "", 0, True
        self.issues = self.issues_from_cache(view)
        self.all_rows = build_issue_rows(self.issues) if self.issues is not None else [("note", "loading issues…")]
        if view:
            write_stored_value("last-view", view)
        self.refresh_issues_in_background()

    def take_in_what_the_background_fetched(self):
        if self.refreshed_issues_waiting is not None:
            (fetched_for_view, issues), self.refreshed_issues_waiting = self.refreshed_issues_waiting, None
            if self.issue_row_numbers:
                self.identifier_to_put_cursor_on = self.issue_under_cursor()["identifier"]
            if fetched_for_view != self.view:  # the view was switched while that fetch ran
                self.refresh_issues_in_background()
            else:
                self.issues, self.all_rows = issues, build_issue_rows(issues)
                if fetched_for_view is None:
                    threading.Thread(target=self.scan_workspaces_and_worktrees, args=(issues,), daemon=True).start()
                    self.greet_on_first_open_of_the_day(issues)
        if self.refresh_error:
            self.footer_message, self.refresh_error = self.refresh_error, ""
        if FAILED_CHANGE_MESSAGES:
            self.footer_message = FAILED_CHANGE_MESSAGES.pop(0)

    def greet_on_first_open_of_the_day(self, issues):
        """Once a day: what moved since yesterday, and Col starts on the daily report and offers to show it."""
        if read_stored_value("digest") != self.today:
            write_stored_value("digest", self.today)
            changes = changes_since_snapshot(self.states_before_first_refresh, issues)
            col_says("morning. here's what moved while you were away" if changes else "morning. all quiet since yesterday", "cheer", 12)
            if changes:
                show_morning_digest(self.screen, changes)
        if DAILY_REPORT.get("date") != self.today:  # first open today: Col starts writing, and asks before showing
            start_writing_daily_report(issues, self.today)
            col_says("morning. I'm writing up your last two days", "cheer", 12)
            wants_to_read_it_now = choose_from_list(
                self.screen, "Col  ·  good morning", ["yes, what have I been up to?", "not now · b opens it later"], help=(
                    "Col sniffs through your in-progress issues, Slack and the workspaces open here, and writes a "
                    "short recap of the last two days so you can pick the threads back up.",
                    "It's being written now and takes about a minute. b opens it any time today.")) == 0
            if wants_to_read_it_now:
                self.identifier_to_put_cursor_on = show_daily_report(self.screen, issues, self.today) or self.identifier_to_put_cursor_on

    def work_out_visible_rows_and_cursor(self):
        self.visible_rows = rows_matching_filter(self.all_rows, self.filter_text)
        self.issue_row_numbers = [row_number for row_number, (kind, _) in enumerate(self.visible_rows) if kind == "issue"]
        # follow the issue, not the index: a refresh that reorders the list keeps the cursor on the same issue
        self.cursor = next((position for position, row_number in enumerate(self.issue_row_numbers)
                            if self.visible_rows[row_number][1]["identifier"] == self.identifier_to_put_cursor_on),
                           min(self.cursor, max(len(self.issue_row_numbers) - 1, 0)))
        self.identifier_to_put_cursor_on = None
        if self.show_bark_for_a_new_list and self.issue_row_numbers:
            self.show_bark_for_a_new_list = False
            col_says("woof", "woof", 1.5)  # a new pile to look at: bark
        UiState.title_of_open_dialog = None  # back on the list: the next dialog pops open again
        identifier_under_cursor = self.issue_under_cursor()["identifier"] if self.issue_row_numbers else None
        if identifier_under_cursor != self.identifier_last_under_cursor:
            self.cursor_came_to_rest_at = time.time()
            if identifier_under_cursor:
                write_stored_value("last", {"sel": identifier_under_cursor})
            self.identifier_last_under_cursor = identifier_under_cursor

    def issue_under_cursor(self):
        return self.visible_rows[self.issue_row_numbers[self.cursor]][1]

    def workspace_summary(self, issue, with_agent_status_and_branch=False):
        """The green marker's words: which workspace(s) and which repositories, e.g. "ws 4 · my-repo", or
        "ws 3 + 6 · my-repo" when the issue has two; the long form adds what each agent is doing and the branch."""
        workspaces = self.open_workspaces_by_identifier[issue["identifier"]]
        repositories = list(self.worktree_folders_by_branch.get(issue.get("branchName"), {}))
        if with_agent_status_and_branch:
            parts = [" + ".join(f"ws {workspace['number']} {workspace.get('agent_status') or '?'}" for workspace in workspaces)]
        else:
            parts = ["ws " + " + ".join(str(workspace["number"]) for workspace in workspaces)]
        if repositories:
            parts.append(", ".join(repositories))
            if with_agent_status_and_branch:
                parts.append(issue["branchName"])
        return " · ".join(parts)

    # ── drawing ───────────────────────────────────────────────────────────────────────────────────────────────────────

    def draw(self):
        """The whole list screen, without refresh. Dialogs call it (through UiState.draw_screen_behind_dialog) to
        repaint what is behind them."""
        self.screen.erase()
        # ponytail: terminals can't blur, so while UiState.grey_out_behind_dialog is on every colour goes dim grey
        self.colour = (lambda pair: curses.color_pair(DIM)) if UiState.grey_out_behind_dialog else curses.color_pair
        self.draw_title_and_view_line()
        if self.nothing_is_assigned_to_me():
            self.draw_nothing_assigned()
            return
        self.draw_rows()
        self.draw_col_in_his_corner()
        self.draw_footer()

    def draw_title_and_view_line(self):
        screen, colour = self.screen, self.colour
        screen_height, screen_width = screen.getmaxyx()
        animation_frame = int(time.time() * 8)
        # breadcrumbs: the view you're in is the filled tag; the one you came from is plain, with the key back
        view_tag = (colour(DONE_FLASH) if time.time() < self.header_flash_until else colour(COL_ACCENT) | curses.A_REVERSE) | curses.A_BOLD
        if not self.view:
            breadcrumbs = [(" my issues ", view_tag), ("  t team · T project · R reviews", colour(DIM))]
        else:
            team_key = self.view["key"] + " · " if self.view["kind"] == "team" else ""
            what_is_listed = "  everyone's open issues in the team" if self.view["kind"] == "team" else "  open issues in the project"
            breadcrumbs = [("h ‹ ", colour(DIM)), ("my issues", colour(MUTED)), ("  ›  ", colour(DIM)),
                           (f" {team_key}{self.view['name'][:40]} ", view_tag), (what_is_listed, colour(MUTED))]
        x = 1
        for text, attribute in breadcrumbs:
            screen.addnstr(2, x, text, max(screen_width - x - 1, 0), attribute)
            x += len(text)
        if self.refresh_is_running:
            sync_or_count = f"{SPINNER_FRAMES[animation_frame % len(SPINNER_FRAMES)]} syncing"
        else:
            sync_or_count = f"{len(self.issue_row_numbers)} issues" + (f"  ·  ✓ {self.done_today_count} done today" if self.done_today_count else "")
        screen.addnstr(2, max(screen_width - len(sync_or_count) - 2, 0), sync_or_count, len(sync_or_count),
                    colour(ACCENT if self.refresh_is_running else DIM))
        # the app's name with Col's eyes and blaze: a solid blue badge centred on the top row, sparks either side when there's room
        app_name = "^ ▐▌ ^  C O L L I N E A R"
        name_x = max((screen_width - len(app_name)) // 2, 2)
        screen.addnstr(0, name_x - 2, "  " + app_name + "  ", max(min(len(app_name) + 4, screen_width - name_x + 1), 0),
                    colour(COL_ACCENT) | curses.A_REVERSE | curses.A_BOLD)
        screen.addnstr(0, name_x, "^ ▐▌ ^", max(min(6, screen_width - name_x - 1), 0), colour(COL_BADGE) | curses.A_BOLD)  # the eyes and the blaze in white
        # the whole badge is blue; C O L is written in the darkest tone and L I N E A R in a slightly greyer one,
        # so the dog's name is what you read first
        linear_x = name_x + len("^ ▐▌ ^  C O L")
        screen.addnstr(0, linear_x, app_name[len("^ ▐▌ ^  C O L"):], max(min(len(" L I N E A R"), screen_width - linear_x - 1), 0),
                       colour(COL_BADGE_QUIET_TEXT) | curses.A_BOLD)
        if name_x >= 5 and name_x + len(app_name) + 5 < screen_width:
            screen.addnstr(0, name_x - 5, "✦ ", 2, colour(COL_FACE) | curses.A_BOLD)
            screen.addnstr(0, name_x + len(app_name) + 3, " ✦", 2, colour(COL_FACE) | curses.A_BOLD)
        screen.addnstr(self.FIRST_LIST_ROW - 1, 1, "─" * (screen_width - 2), screen_width - 2, colour(BORDER))

    def nothing_is_assigned_to_me(self):
        return bool(self.all_rows) and self.all_rows[0][0] == "note" and not self.view and self.issues is not None and not self.issues

    def draw_nothing_assigned(self):
        screen_height, screen_width = self.screen.getmaxyx()
        lines = (("◌", ACCENT), ("", 0), ("nothing assigned to you", MUTED), ("", 0),
                 ("n  create an issue      t  browse a team      T  browse a project", DIM))
        for line_number, (text, colour_pair) in enumerate(lines):
            self.screen.addnstr(screen_height // 2 - 3 + line_number, max((screen_width - len(text)) // 2, 0), text, screen_width - 2,
                             self.colour(colour_pair) | curses.A_BOLD)
        draw_footer_hints(self.screen, None, 2, self.footer_hints(only_those_that_work_on_an_empty_list=True))

    def draw_rows(self):
        screen_height, screen_width = self.screen.getmaxyx()
        rows_that_fit = screen_height - self.FIRST_LIST_ROW - self.ROWS_BELOW_THE_LIST
        row_under_cursor = self.issue_row_numbers[self.cursor] if self.issue_row_numbers else 0
        first_row_shown = max(0, row_under_cursor - rows_that_fit + 1)
        position_among_issues = {row_number: position for position, row_number in enumerate(self.issue_row_numbers)}
        now = datetime.now(timezone.utc)
        for y, (kind, value) in enumerate(self.visible_rows[first_row_shown:first_row_shown + rows_that_fit], start=self.FIRST_LIST_ROW):
            row_number = first_row_shown + y - self.FIRST_LIST_ROW
            if kind == "head":
                self.draw_state_header_row(y, row_number, state_name=value[0], state_type=value[1])
            elif kind == "note":
                self.screen.addnstr(y, 3, value, screen_width - 4, self.colour(MUTED))
            elif kind == "issue":
                self.draw_issue_row(y, value, rows_from_cursor=position_among_issues[row_number] - self.cursor, now=now)

    def draw_state_header_row(self, y, row_number, state_name, state_type):
        """"◐ In Progress 3 ──────": the state, how many issues it has, and a thin rule to the right edge."""
        screen, colour = self.screen, self.colour
        screen_width = screen.getmaxyx()[1]
        rows_below = self.visible_rows[row_number + 1:]
        issue_count = str(next((n for n, row in enumerate(rows_below) if row[0] != "issue"), len(rows_below)))
        icon, icon_colour = progress_icon_for_state(state_name, state_type)
        screen.addnstr(y, 2, icon, 2, colour(icon_colour) | curses.A_BOLD)
        screen.addnstr(y, 4, state_name, screen_width - 5, colour(ACCENT) | curses.A_BOLD)
        screen.addnstr(y, 5 + len(state_name), issue_count, max(screen_width - 6 - len(state_name), 0), colour(DIM))
        rule_x = 7 + len(state_name) + len(issue_count)
        screen.addnstr(y, rule_x, "─" * max(screen_width - 2 - rule_x, 0), max(screen_width - 2 - rule_x, 0), colour(BORDER))

    def draw_issue_row(self, y, issue, rows_from_cursor, now):
        """One issue. Columns: ▎ distance ▶ id · priority · title … assignee chip (team/project view) · project ·
        workspace · PR badge · age."""
        screen, colour = self.screen, self.colour
        screen_width = screen.getmaxyx()[1]
        is_under_cursor = rows_from_cursor == 0
        has_open_workspace = issue["identifier"] in self.open_workspaces_by_identifier
        if not self.is_typing_filter:  # click a row: the cursor goes there; click the cursor's row: preview it
            register_clickable_row(y, 0, screen_width, rows_from_cursor, [ord(" ")] * is_under_cursor)
        cursor_row_attribute = colour(SELECTED_ROW) if is_under_cursor else 0

        def styled(colour_pair, bold=False):  # on the cursor's row everything wears the cursor row's colours
            return (cursor_row_attribute if is_under_cursor else colour(colour_pair)) | (curses.A_BOLD if bold else 0)

        is_narrow_popup = screen_width < 110  # bars only for priority and no assignee, so the title keeps its room
        priority_bars, priority_word, priority_colour = PRIORITY_DISPLAY.get(issue["priority"], ("▯▯▯", "", DIM))
        priority_text = priority_bars if is_narrow_popup else f"{priority_bars} {priority_word}"
        priority_width = 4 if is_narrow_popup else 11
        badge_text, badge_colour = pull_request_badge(issue)
        days_quiet = self.days_quiet_by_identifier.get(issue["identifier"], 0)
        if not badge_text and days_quiet >= 3:
            badge_text, badge_colour = f"· quiet {days_quiet}d", DIM

        right_columns = []  # (text, colour, drawn as a filled chip)
        if self.view and not is_narrow_popup:
            assignee_name = (issue.get("assignee") or {}).get("displayName", "")
            initials, chip_colour = initials_and_chip_colour(assignee_name)
            right_columns.append((f" {initials} ", chip_colour, True))
            right_columns.append((f"{(assignee_name or '—')[:11]:<11}", MUTED, False))
            if screen_width >= 140:
                project_name = ((issue.get("project") or {}).get("name") or "")[:11]
                cycle = f" c{issue['cycle']['number']}" if issue.get("cycle") else ""
                right_columns.append((f"{(project_name + cycle)[:15]:<15}", DIM, False))
        if has_open_workspace:  # a herdr workspace is open for it: say which, and where its code is; w jumps there
            workspace_numbers = " + ".join(str(workspace["number"]) for workspace in self.open_workspaces_by_identifier[issue["identifier"]])
            right_columns.append((f" ▶ ws {workspace_numbers} " if is_narrow_popup else f" ▶ {self.workspace_summary(issue)} ", GREEN, True))
            branch = issue.get("branchName") or ""
            if screen_width >= 170 and branch in self.worktree_folders_by_branch:  # room for the branch too
                right_columns.append((f"{branch[:27] + '…' if len(branch) > 28 else branch:<28}", GREEN, False))
        right_columns += [(f"{badge_text:<13}", badge_colour, False),
                          (f"{time_since_text(issue['updatedAt'], now):>7}", colour_for_issue_age(issue["updatedAt"], now), False)]
        right_columns_width = sum(len(text) for text, _, _ in right_columns) + 2 * (len(right_columns) - 1)

        if is_under_cursor:
            screen.addnstr(y, 1, " " * (screen_width - 2), screen_width - 2, cursor_row_attribute)
            screen.addnstr(y, 1, "▎", 1, colour(SELECTED_ROW_MARKER))
        else:  # relative numbers, like nvim: 3j lands on the row marked 3
            screen.addnstr(y, 2, f"{abs(rows_from_cursor):>2}"[-2:], 2, styled(DIM))
        if has_open_workspace:
            screen.addnstr(y, 4, "▶", 1, styled(GREEN, True))
        state_colour = progress_icon_for_state(issue["state"]["name"], issue["state"]["type"])[1]
        screen.addnstr(y, 6, f"{issue['identifier']:<9}", screen_width - 7, styled(state_colour, is_under_cursor))
        screen.addnstr(y, 16, priority_text, priority_width, styled(priority_colour, issue["priority"] in (1, 2)))
        title_x = 16 + priority_width + 1
        title_width = max(screen_width - 4 - right_columns_width - title_x, 0)
        title = issue["title"] if len(issue["title"]) <= title_width else issue["title"][:max(title_width - 1, 0)] + "…"
        screen.addnstr(y, title_x, title, title_width, cursor_row_attribute | (curses.A_BOLD if is_under_cursor else 0))
        x = max(screen_width - 2 - right_columns_width, title_x)
        for text, colour_pair, is_chip in right_columns:
            attribute = (colour(colour_pair) | curses.A_REVERSE | curses.A_BOLD) if is_chip and not is_under_cursor else \
                styled(colour_pair, is_chip or colour_pair in (RED, GREEN))
            screen.addnstr(y, x, text, max(screen_width - 2 - x, 0), attribute)
            x += len(text) + 2

    def col_mood_and_words(self):
        """(which face, what he says) right now: what he's working on, something he just said, asleep after three
        quiet minutes, a sniff now and then, otherwise a tip or a dog thought."""
        now, animation_frame = time.time(), int(time.time() * 8)
        if UiState.guided_tour_step is not None:  # the tour: he says the step he is waiting for
            return "cheer", words_of_guided_tour_step()
        if UiState.col_is_working:  # on a job: eyes left and right, a sniff, and what he's at
            lookups, beat = UiState.lookups_col_is_working_on, int(now * 1.3)  # the clock, not a frame count: another screen's loop is running
            mood = ("left", "cheer", "sniff", "cheer", "right", "cheer")[beat % 6]
            words = COL_WORKING_CHATTER[beat // 4 % len(COL_WORKING_CHATTER)] if beat // 4 % 3 == 2 else \
                col_words_for_lookup(lookups[-1]) if lookups else "reading it twice"
        elif now < COL_SPEECH["until"]:
            mood, words = COL_SPEECH["mood"], COL_SPEECH["say"]
        elif now - self.cursor_came_to_rest_at > 180:
            mood, words = "sleep", "zzz"
        elif now % 37 < 2.5:  # now and then: a sniff, nose twitching
            mood, words = "sniff", "sniff sniff"
        else:  # tips, with a dog thought every fourth slot; happy eyes most of the time, wide eyes only now and then
            slot = int(now // 45)
            mood = "calm" if slot % 5 == 4 else "cheer"
            words = COL_IDLE_THOUGHTS[slot // 4 % len(COL_IDLE_THOUGHTS)] if slot % 4 == 3 else COL_TIPS[slot % len(COL_TIPS)]
        if mood == "woof":  # bark: mouth open / closed, the word pulses
            mood, words = ("woof", "WOOF!") if animation_frame // 2 % 2 else ("cheer", "woof")
        elif mood == "sniff" and animation_frame // 2 % 2:
            mood = "cheer"
        return mood, words

    def draw_col_in_his_corner(self):
        """Col bottom right in a thin rounded outline with his name tag, and what he says in a bubble to his left."""
        if not UiState.col_is_visible:
            return
        screen, colour = self.screen, self.colour
        screen_height, screen_width = screen.getmaxyx()
        mood, words = self.col_mood_and_words()
        face = COL_FACES[mood]
        face_x = screen_width - len(face[2]) - 2
        outline_width = screen_width - face_x
        # while he's on a job the outline pulses thin / heavy
        is_heavy_beat = UiState.col_is_working and not int(time.time() * 3) % 2
        top_left, horizontal, top_right, vertical, bottom_left, bottom_right, tag_left, tag_right = "┏━┓┃┗┛╸╺" if is_heavy_beat else "╭─╮│╰╯╴╶"
        screen.addnstr(screen_height - 6, face_x - 1, top_left + horizontal * (outline_width - 2) + top_right, outline_width, colour(COL_ACCENT))
        for line_number, face_line in enumerate(face):
            y = screen_height - 5 + line_number
            screen.addnstr(y, face_x - 1, vertical, 1, colour(COL_ACCENT))
            screen.addnstr(y, face_x, face_line, len(face_line), colour(COL_FACE) | curses.A_BOLD)
            screen.addnstr(y, screen_width - 2, vertical, 1, colour(COL_ACCENT))
        screen.addnstr(screen_height - 1, face_x - 1, bottom_left + horizontal * (outline_width - 2) + bottom_right, outline_width, colour(COL_ACCENT))
        name_tag_x = face_x - 1 + (outline_width - 7) // 2
        screen.addnstr(screen_height - 1, name_tag_x, f"{tag_left} Col {tag_right}", 7, colour(COL_ACCENT))  # his collar tag
        screen.addnstr(screen_height - 1, name_tag_x + 2, "Col", 3, colour(COL_ACCENT) | curses.A_BOLD)
        bubble = f"“{words}” ‹" if words else ""  # everything he says is in quotes
        screen.addnstr(screen_height - 4, max(face_x - len(bubble) - 1, 0), bubble, max(face_x - 1, 0), colour(COL_ACCENT) | curses.A_BOLD)

    def footer_hints(self, only_those_that_work_on_an_empty_list=False):
        """[(keys, text)] for the footer, from KEY_BINDINGS. Jumping to an open workspace goes first when there is
        one, undo shows only when there is something to undo, and h says where it leads."""
        hints, jump_hint = [], []
        for binding in KEY_BINDINGS:
            if not binding.footer_keys or (only_those_that_work_on_an_empty_list and binding.needs_issue_under_cursor):
                continue
            if binding.action == "jump_to_workspace_of_issue":
                workspace_count = len(self.open_workspaces_by_identifier.get(self.issue_under_cursor()["identifier"], [])) \
                    if self.issue_row_numbers else 0
                if workspace_count:
                    jump_hint = [(binding.footer_keys, binding.footer_text if workspace_count == 1 else f"pick one of its {workspace_count} workspaces")]
            elif binding.action == "undo_last_change" and not UNDO_STACK:
                continue
            elif binding.action == "go_back_or_close" and self.view:
                hints.append((binding.footer_keys, "back to my issues"))
            else:
                hints.append((binding.footer_keys, binding.footer_text))
        return jump_hint + hints

    def draw_footer(self):
        """The bottom row(s): the count being typed, then a message if there is one, else the filter, else the hints."""
        screen, colour = self.screen, self.colour
        screen_height, screen_width = screen.getmaxyx()
        if self.count_typed_before_a_key:
            screen.addnstr(screen_height - 1, screen_width - len(self.count_typed_before_a_key) - 18, self.count_typed_before_a_key,
                        len(self.count_typed_before_a_key), colour(ACCENT) | curses.A_BOLD)
        if self.footer_message:
            # click the message away: key 0 does nothing but clear it, and the hints return
            CLICK_ZONES.append((screen_height - 1, 0, screen_width, [0]))
            is_bad_news = re.search(r"fail|error|no .* linked|no workspace|denied", self.footer_message, re.I)
            room = screen_width - 3 - (COL_CORNER_WIDTH if UiState.col_is_visible else 0)
            screen.addnstr(screen_height - 1, 2, ("✗ " if is_bad_news else "✓ ") + self.footer_message, room,
                        colour(RED if is_bad_news else GREEN) | curses.A_BOLD)
        elif self.is_typing_filter or self.filter_text:
            screen.addnstr(screen_height - 1, 2, f"/ {self.filter_text}{'▏' if self.is_typing_filter else ''}", screen_width - 3,
                        colour(ACCENT) | curses.A_BOLD)
            draw_footer_hints(screen, screen_height - 1, 6 + len(self.filter_text),
                              [("enter", "keep"), ("esc", "clear")] if self.is_typing_filter else [("esc", "clear"), ("?", "all keys")])
        else:
            draw_footer_hints(screen, None, 2, self.footer_hints())

    # ── keys ──────────────────────────────────────────────────────────────────────────────────────────────────────────

    def handle_key(self, key):
        """One key press on the list: typed into the filter, added to the count, or handed to its KEY_BINDINGS action."""
        self.footer_message = ""
        if self.is_typing_filter:
            self.type_into_filter(key)
            return
        is_digit_of_a_count = ord("0") <= key <= ord("9") and (self.count_typed_before_a_key or key != ord("0"))
        if is_digit_of_a_count:  # a count for j/k, like 5j
            self.count_typed_before_a_key += chr(key)
            return
        self.repeat_count, self.count_typed_before_a_key = int(self.count_typed_before_a_key or 1), ""
        key = KEYCODE_ALIASES.get(key, key)
        binding = KEY_BINDING_BY_CHARACTER.get(chr(key)) if 0 < key < 256 else None
        if binding:
            if self.issue_row_numbers or not binding.needs_issue_under_cursor:
                self.run_action(binding.action)
        elif 32 <= key < 127 and chr(key) not in CHARACTERS_BOUND_ON_THE_LIST:  # type to filter, no / needed
            self.is_typing_filter, self.filter_text, self.cursor = True, chr(key), 0
            col_reacts_to("filter")

    def run_action(self, action):
        """Run one KEY_BINDINGS action. What it returns is the footer message; an error becomes the message too,
        instead of the popup silently closing."""
        try:
            self.footer_message = getattr(self, action)() or ""
        except Exception as error:
            self.footer_message = f"{type(error).__name__}: {error}"[:160]

    def type_into_filter(self, key):
        if key == 27:
            self.filter_text, self.is_typing_filter = "", False
            col_reacts_to("unfilter")
        elif key in (10, 13, curses.KEY_ENTER):
            self.is_typing_filter = False
        elif (typed := text_after_typing(self.filter_text, key)) is not None:
            if len(typed) > len(self.filter_text):
                self.cursor = 0  # a new letter: back to the first match
            elif not typed:
                self.is_typing_filter = False  # backspaced it all away
            self.filter_text = typed

    def rebuild_rows_keeping_cursor_on(self, issue):
        self.all_rows, self.identifier_to_put_cursor_on = build_issue_rows(self.issues), issue["identifier"]

    @staticmethod
    def linear_took_the_change(footer_message):
        return "→" in footer_message or "canceled" in footer_message

    # -- move around --

    def move_cursor_down(self):
        self.cursor = min(self.cursor + self.repeat_count, len(self.issue_row_numbers) - 1)

    def move_cursor_up(self):
        self.cursor = max(self.cursor - self.repeat_count, 0)

    def move_cursor_to_last_issue(self):
        self.cursor = len(self.issue_row_numbers) - 1

    def start_typing_a_filter(self):
        self.is_typing_filter, self.filter_text, self.cursor = True, "", 0
        col_reacts_to("filter")

    # -- work on the issue --

    def start_work_on_issue_under_cursor(self):
        return start_work_on_issue(self.screen, self.issue_under_cursor())

    def preview_issue_under_cursor(self):
        """The preview can hand off to starting the issue, asking Col, opening it in Linear or jumping to its workspace."""
        issue = self.issue_under_cursor()
        col_says(COL_PREVIEW_LINES[self.issue_row_numbers[self.cursor] % len(COL_PREVIEW_LINES)], "cheer", 4)
        open_workspace_summary = issue["identifier"] in self.open_workspaces_by_identifier and \
            self.workspace_summary(issue, with_agent_status_and_branch=True)
        chosen_in_preview = show_issue_preview(self.screen, issue, open_workspace_summary)
        action_for_choice = {"launch": "start_work_on_issue_under_cursor", "a": "ask_col_about_issue",
                             "L": "open_issue_in_linear_app", "w": "jump_to_workspace_of_issue"}
        return getattr(self, action_for_choice[chosen_in_preview])() if chosen_in_preview in action_for_choice else ""

    def jump_to_workspace_of_issue(self):
        return jump_to_a_workspace_of_issue(self.screen, self.issue_under_cursor())

    def ask_col_about_issue(self):
        col_reacts_to("ask")
        return chat_with_col(self.screen, self.issue_under_cursor())

    def comment_on_issue(self):
        footer_message = write_or_draft_comment(self.screen, self.issue_under_cursor())
        if footer_message.startswith("commented"):
            col_reacts_to("comment", "cheer")
        return footer_message

    def start_clean_slate_on_issue(self):
        return start_clean_slate(self.screen, self.issue_under_cursor())

    # -- change the issue --

    def change_status_of_issue(self):
        issue = self.issue_under_cursor()
        footer_message = choose_new_status(self.screen, issue)
        if self.linear_took_the_change(footer_message):
            self.col_cheers_for_new_state_of(issue)
            if issue["state"]["type"] == "completed":
                footer_message = self.count_one_more_done_today(issue)
            self.rebuild_rows_keeping_cursor_on(issue)
        return footer_message

    def change_priority_of_issue(self):
        issue = self.issue_under_cursor()
        footer_message = choose_new_priority(self.screen, issue)
        if self.linear_took_the_change(footer_message):
            col_reacts_to("prio", "cheer")
            self.rebuild_rows_keeping_cursor_on(issue)
        return footer_message

    def change_assignee_of_issue(self):
        issue = self.issue_under_cursor()
        footer_message = choose_new_assignee(self.screen, issue)
        if self.linear_took_the_change(footer_message):
            col_reacts_to("assign", "cheer")
            self.rebuild_rows_keeping_cursor_on(issue)
        return footer_message

    def cancel_issue_under_cursor(self):
        issue = self.issue_under_cursor()
        footer_message = confirm_and_cancel_issue(self.screen, issue)
        if not footer_message:
            col_reacts_to("cancel_no")
        elif self.linear_took_the_change(footer_message):
            self.col_cheers_for_new_state_of(issue)
            self.rebuild_rows_keeping_cursor_on(issue)
        return footer_message

    def col_cheers_for_new_state_of(self, issue):
        if lines := COL_LINES_BY_NEW_STATE_TYPE.get(issue["state"]["type"]):
            col_says(lines[int(time.time()) % len(lines)], "cheer")

    def count_one_more_done_today(self, issue):
        """An issue was just moved to done: count it, flash the header, Col cheers. Returns the footer message."""
        self.done_today_count += 1
        write_stored_value("done", {"date": self.today, "n": self.done_today_count})
        self.header_flash_until = time.time() + 0.6
        col_says(COL_CHEERS_FOR_DONE[self.done_today_count % len(COL_CHEERS_FOR_DONE)].format(n=self.done_today_count), "cheer", 10)
        return f"{issue['identifier']} → {issue['state']['name']}  ·  {self.done_today_count} done today  ·  U undo"

    def undo_last_change(self):
        if not UNDO_STACK:
            col_reacts_to("nothing")
            return "nothing to undo"
        issue, input_that_puts_it_back, fields_to_put_back, label = UNDO_STACK.pop()
        change_issue_optimistically(issue, input_that_puts_it_back, fields_to_put_back, label, undo=False)
        self.rebuild_rows_keeping_cursor_on(issue)
        col_says("undone, no harm done")
        return f"undid {label}"

    def create_new_issue(self):
        try:
            return create_issue_and_write_it(self.screen)
        finally:
            col_says("fresh issue. write it so a teammate could pick it up", "cheer")
            self.refresh_issues_in_background()

    # -- open elsewhere --

    def open_pull_request_of_issue(self):
        links = linked_pull_requests(self.issue_under_cursor())
        if not links:
            return "no PR linked to this issue"
        name_of = lambda url: f"{url.split('/')[-3]} #{url.split('/')[-1]}"
        picked = 0 if len(links) == 1 else choose_from_list(self.screen, "open PR", [f"{name_of(url)}  {title}" for url, title in links])
        if picked is None:
            return ""
        subprocess.run(["open", links[picked][0]])
        col_reacts_to("pr", "cheer")
        return f"opened {name_of(links[picked][0])}"

    def open_issue_in_linear_app(self):
        issue = self.issue_under_cursor()
        # linear:// opens the desktop app; ponytail: no app installed -> macOS shows an error, swap back to the https url
        subprocess.run(["open", issue["url"].replace("https://linear.app/", "linear://", 1)])
        col_reacts_to("open")
        return f"opened {issue['identifier']} in Linear"

    def copy_link_of_issue(self):
        issue = self.issue_under_cursor()
        subprocess.run(["pbcopy"], input=issue["url"], text=True)
        col_reacts_to("copy")
        return f"copied {issue['identifier']} link"

    # -- views --

    def open_daily_report(self):
        if identifier_picked_in_report := show_daily_report(self.screen, self.issues or [], self.today):
            self.identifier_to_put_cursor_on, self.filter_text, self.is_typing_filter = identifier_picked_in_report, "", False
            col_reacts_to("pick")  # the list lands on that issue

    def browse_a_team(self):
        _, team = choose_team(self.screen, "browse team")
        if team:
            self.switch_to_view(team)
            col_reacts_to("team")

    def browse_a_project(self):
        if project := choose_project(self.screen):
            self.switch_to_view(project)
            col_reacts_to("project")

    def open_review_inbox(self):
        col_reacts_to("inbox")
        return show_review_inbox(self.screen)

    def refresh_issues_now(self):
        self.refresh_issues_in_background()
        col_reacts_to("refresh")

    def show_every_key(self):
        show_key_reference(self.screen)
        col_says("that's all of them. you've got this", "cheer")

    def go_back_or_close(self):
        """Back one step at a time: clear the filter, then from a team or project to my issues, then close."""
        if self.filter_text:
            self.filter_text = ""
            col_reacts_to("unfilter")
        elif self.view:
            self.switch_to_view(None)
            col_reacts_to("back")
        else:
            self.popup_should_close = True

    def close_popup(self):
        self.popup_should_close = True

    def toggle_col(self):
        UiState.col_is_visible = not UiState.col_is_visible
        write_stored_value("col", {"on": UiState.col_is_visible})
        if not UiState.col_is_visible:
            return "Col is off duty  ·  Z brings him back"
        col_says("missed you too", "cheer")
        return "Col is back"

    def toggle_footer_hints(self):
        UiState.footer_hints_visible = not UiState.footer_hints_visible
        write_stored_value("hints", {"on": UiState.footer_hints_visible})
        return "key hints are back" if UiState.footer_hints_visible else "key hints off  ·  ? still lists every key, K brings them back"

    def give_col_a_bone(self):
        """B: a bone for Col. He holds it for a few seconds and says thanks; today's bones are counted."""
        if not UiState.col_is_visible:
            return "Col is off duty, the bone waits  ·  Z brings him back"
        bones = read_stored_value("bones") or {}
        bones_today = (bones.get("n", 0) if bones.get("date") == self.today else 0) + 1
        write_stored_value("bones", {"date": self.today, "n": bones_today})
        thanks = COL_THANKS_FOR_A_BONE[(bones_today - 1) % len(COL_THANKS_FOR_A_BONE)] if bones_today <= MANY_BONES_IN_A_DAY else \
            f"bone number {bones_today}. I'm going to need a bigger hole"
        col_says(thanks, "bone", 6)
        return f"gave Col a bone  ·  {bones_today} today"

    def show_the_tour_again(self):
        start_guided_tour(self.screen)

    def open_setup(self):
        run_setup(self.screen, force=True)
        self.refresh_issues_in_background()  # the key may be someone else's now


def main(screen):
    screen = ScreenWithMouse(screen)
    curses.curs_set(0)
    curses.set_escdelay(25)
    set_up_colour_pairs()
    screen.timeout(250)
    IssueListScreen(screen).run()


def _test():
    global REPOSITORIES_FOLDER
    mk = lambda ident, state, typ, pos, prio=0: {"identifier": ident, "title": f"t {ident}", "priority": prio,
                                                  "state": {"name": state, "type": typ, "position": pos}}
    issues = [mk("A-1", "Todo", "unstarted", 1), mk("A-2", "In Progress", "started", 2, 3),
              mk("A-3", "In Review", "started", 3), mk("A-4", "In Progress", "started", 2, 1)]
    rows = build_issue_rows(issues)
    flat = lambda rs: [v[0] if k == "head" else v["identifier"] if k == "issue" else "" for k, v in rs]
    assert flat(rows) == ["In Review", "A-3", "In Progress", "A-4", "A-2", "Todo", "A-1"]
    assert rows[0] == ("head", ("In Review", "started"))
    assert flat(build_issue_rows([mk("B-1", "Triage", "triage", 0), mk("B-2", "Backlog", "backlog", 0), issues[0]])) == \
        ["Todo", "A-1", "Backlog", "B-2", "Triage", "B-1"]
    assert flat(rows_matching_filter(rows, "a-2")) == ["In Progress", "A-2"]
    assert flat(rows_matching_filter(rows, "")) == ["In Review", "A-3", "", "In Progress", "A-4", "A-2", "", "Todo", "A-1"]
    assert build_issue_rows([])[0][0] == "note" and rows_matching_filter([("note", "loading")], "x") == [("note", "loading")]
    assert flat(build_issue_rows(issues + [mk("Z-1", "Done", "completed", 9)])) == flat(rows), "closed issues drop out of the list"
    assert initials_and_chip_colour("sam.river")[0] == "SR" and initials_and_chip_colour("Robin")[0] == "R" and initials_and_chip_colour("")[0] == "?"
    assert initials_and_chip_colour("sam.river")[1] == initials_and_chip_colour("sam.river")[1]
    now = datetime.now(timezone.utc)
    stamp = lambda d: (now - __import__("datetime").timedelta(days=d)).isoformat()
    assert [colour_for_issue_age(stamp(d), now) for d in (0, 3, 15, 30)] == [DEFAULT_TEXT, MUTED, DIM, RED]
    old = [dict(mk("A-1", "Todo", "unstarted", 1), attachments={"nodes": []})]
    new = [dict(mk("A-1", "Done", "completed", 9), attachments={"nodes": []}), dict(mk("A-9", "Todo", "unstarted", 1), attachments={"nodes": []})]
    assert changes_since_snapshot(snapshot_of_states_and_badges(old), new) == [("A-1", "t A-1", "Todo → Done"), ("A-9", "t A-9", "new")]
    assert changes_since_snapshot(snapshot_of_states_and_badges(new), new) == []
    issue = {"identifier": "PLA-9", "title": "Fix it", "url": "u", "branchName": "erik/pla-9"}
    for phase, _ in PHASES:
        text = render_phase_prompt(phase, issue, ["/wt/a", "/wt/b"], "/notes/PLA-9")
        assert "- /wt/a\n- /wt/b" in text
        assert "PLA-9" in text and "erik/pla-9" in text and "{" not in text, phase
    assert "- none:" in render_phase_prompt("issue", issue, [], "/notes/PLA-9")
    clean = render_phase_prompt("clean", issue, [], "/notes/PLA-9")
    assert "PLA-9" in clean and "/notes/PLA-9" in clean and "worktree" not in clean and "{" not in clean
    att = {"attachments": {"nodes": [{"url": "https://github.com/o/r/pull/7", "title": "x"},
                                     {"url": "https://example.slack.com/archives/C1/p1", "title": "slack"}]}}
    assert linked_pull_requests(att) == [("https://github.com/o/r/pull/7", "x")] and linked_pull_requests({}) == []
    real = next(p.name for p in REPOSITORIES_FOLDER.iterdir() if (p / ".git").is_dir())
    pr_att = {"attachments": {"nodes": [{"url": f"https://github.com/o/{real}/pull/9", "metadata": {"branch": "fix/x", "number": 9, "repoName": real}},
                                        {"url": f"https://github.com/o/{real}/pull/9", "metadata": {"branch": "fix/x", "number": 9}},
                                        {"url": "https://github.com/o/nope-not-a-repo/pull/1", "metadata": {"branch": "b"}}]}}
    assert linked_pull_request_branches(pr_att) == [(real, "fix/x", 9)] and linked_pull_request_branches(att) == []
    plain = lambda ls: ["".join(t for t, _ in l) for l in ls]
    md = markdown_to_styled_lines("## Risks\n- **circular** imports in `Answer`\n  1. nested\n> quoted\n```\nnpm test\n```\n---\nsee [docs](http://x) _now_", 30)
    assert plain(md) == ["Risks", "• circular imports in Answer", "  1. nested", "│ quoted", "  npm test", "─" * 30, "see docs now"], plain(md)
    assert md[0] == [("Risks", "head")] and ("circular ", "b") in md[1] and ("Answer", "code") in md[1]
    assert ("docs ", "link") in md[-1] and ("now", "i") in md[-1]
    wrapped = plain(markdown_to_styled_lines("- " + "word " * 12, 20))
    assert all(len(l) <= 20 for l in wrapped) and wrapped[1].startswith("  word"), wrapped
    assert plain(markdown_to_styled_lines("a **b** c", 20)) == ["a b c"]
    assert markdown_to_styled_lines("**`__dirname`**: x", 30)[0][0] == ("__dirname", "code")
    assert describe_tool_call({"name": "mcp__claude_ai_Slack__slack_read_thread"}) == "Slack: read thread"
    assert describe_tool_call({"name": "Bash", "input": {"command": "gh pr checks 7"}}) == "gh pr checks 7"
    assert not any("save_" in t or "delete" in t or "Linear__create" in t for t in tools_col_may_use(DEFAULT_CONNECTOR_NAMES)), "Linear must stay read-only"
    listing = ("Checking MCP server health…\n\nclaude.ai Linear: https://x - ! Needs authentication\nlinear-server: npx linear - ✔ Connected\n"
               "claude.ai Slack: https://y - ✔ Connected\nplugin:tools:Notion: https://z - ✔ Connected\n")
    servers = connectors_listed_by_claude(listing)
    assert ("claude.ai Linear", False) in servers and ("plugin:tools:Notion", True) in servers and len(servers) == 4
    assert connector_for_service("Linear", servers) == ("linear-server", True), "a connected server wins over a claude.ai one that isn't"
    assert connector_for_service("Slack", servers) == ("claude.ai Slack", True) and connector_for_service("Jira", servers) == (None, False)
    assert mcp_tool_prefix("claude.ai Linear") == "mcp__claude_ai_Linear__" and mcp_tool_prefix("linear-server") == "mcp__linear-server__"
    assert "mcp__linear-server__get_issue" in tools_col_may_use({"Linear": "linear-server", "Slack": "claude.ai Slack"})
    import prs, tempfile
    real_settings_file, real_settings, real_folder = prs.LOCAL_SETTINGS_FILE, dict(prs.LOCAL_SETTINGS), REPOSITORIES_FOLDER
    with tempfile.TemporaryDirectory() as temporary_folder:  # F in setup: switching the repositories folder
        prs.LOCAL_SETTINGS_FILE = Path(temporary_folder) / "local_settings.json"
        (Path(temporary_folder) / "one-repository" / ".git").mkdir(parents=True)
        assert set_repositories_folder(temporary_folder + "/missing").startswith("no such folder") and REPOSITORIES_FOLDER == real_folder
        assert set_repositories_folder(temporary_folder) == "repositories folder saved: 1 git repository in it"
        assert REPOSITORIES_FOLDER == prs.REPOSITORIES_FOLDER == Path(temporary_folder) and local_repository_names() == ["one-repository"]
        saved = json.loads(prs.LOCAL_SETTINGS_FILE.read_text())
        assert Path(saved["repositories_folder"]).expanduser() == Path(temporary_folder) and saved.get("linear_workspace") == real_settings.get("linear_workspace")
    prs.LOCAL_SETTINGS_FILE, REPOSITORIES_FOLDER, prs.REPOSITORIES_FOLDER = real_settings_file, real_folder, real_folder
    prs.LOCAL_SETTINGS.clear()
    prs.LOCAL_SETTINGS.update(real_settings)
    assert shortened_folder(Path.home() / "code", 18) == "~/code" and shortened_folder(Path("/a/very/long/path/to/the/repositories"), 18) == "…/the/repositories"
    assert herdr_version_from("herdr 0.9.5") == (0, 9, 5) and herdr_version_from("herdr 0.10.0-preview") == (0, 10, 0) and herdr_version_from("?") is None
    pr = lambda **m: {"url": "https://github.com/o/r/pull/1", "metadata": m}
    assert pull_request_badge_key({"status": "inReview", "hasConflicts": True}) == "conflict"
    assert pull_request_badge_key({"status": "open", "draft": True}) == "draft" and pull_request_badge_key({"status": "inReview"}) == "review"
    assert pull_request_badge_key({"status": "merged", "hasConflicts": True}) == "merged" and pull_request_badge_key(None) == "linked"
    assert pull_request_badge({"attachments": {"nodes": []}}) == ("", 0)
    assert pull_request_badge({"attachments": {"nodes": [pr(status="merged"), pr(status="inReview")]}}) == ("● review ×2", YELLOW)
    assert pull_request_badge({"attachments": {"nodes": [pr(status="merged")]}}) == ("✓ merged", PURPLE)
    assert keycodes_named_in_hint("l/enter") == [(0, 1, ord("l")), (2, 5, 10)] and keycodes_named_in_hint("/") == [(0, 1, ord("/"))]
    assert keycodes_named_in_hint("ctrl-d/u") == [(5, 1, 4), (7, 1, 21)] and keycodes_named_in_hint("type") == [] and keycodes_named_in_hint("esc/h")[0][2] == 27
    CLICK_ZONES[:] = [(3, 0, 80, [10])]
    register_clickable_row(3, 10, 20, -2, [32])
    assert keys_for_click_at(3, 12) == [curses.KEY_UP, curses.KEY_UP, 32] and keys_for_click_at(3, 40) == [10] and keys_for_click_at(4, 12) == []
    bound_characters = [character for binding in KEY_BINDINGS for character in binding.keys]
    assert len(bound_characters) == len(set(bound_characters)), "a key is bound twice"
    assert all(callable(getattr(IssueListScreen, binding.action)) for binding in KEY_BINDINGS if binding.keys), "a key names a missing action"
    assert all(binding.reference_group in REFERENCE_GROUPS_IN_ORDER + ("",) for binding in KEY_BINDINGS)
    assert "B" in CHARACTERS_BOUND_ON_THE_LIST and "m" not in CHARACTERS_BOUND_ON_THE_LIST and "5" in CHARACTERS_BOUND_ON_THE_LIST
    assert [keys for group, rows in KEY_REFERENCE_GROUPS if group == "Open elsewhere" for keys, _ in rows] == ["g", "L", "y"]
    assert text_after_typing("ab", 127) == "a" and text_after_typing("a", "ö") == "aö" and text_after_typing("a", ord("b")) == "ab"
    assert text_after_typing("a", 27) is None and text_after_typing("a", "\n") is None and text_after_typing("a", curses.KEY_UP) is None
    DAILY_REPORT["cur"] = 1
    report = "## plate\n- **PLA-1 one** — fine\n- **loose thing** — nobody filed it\n"
    assert report_topic_under_cursor(report) == ReportTopic(1, "loose thing — nobody filed it", "")
    DAILY_REPORT["cur"] = 0
    assert report_topic_under_cursor(report).issue_identifier == "PLA-1" and report_topic_under_cursor("no bullets") is None
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        _test()
    else:
        locale.setlocale(locale.LC_ALL, "")
        curses.wrapper(main)
