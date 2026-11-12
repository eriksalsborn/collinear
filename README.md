<p align="center">
  <img src="assets/banner.svg" alt="COLLINEAR — Linear issues inside herdr, herded by Col the border collie" width="100%">
</p>

Collinear is a popup for [herdr](https://herdr.dev) that shows your Linear issues and turns one into a running
coding session in a single key press, so you never lose track of what you are working on, where its code is checked
out, or which workspace it lives in.

Col is the border collie in the corner. He herds the issues, writes your daily report, answers questions about an
issue, and accepts bones.

## Install

You need macOS, [herdr](https://herdr.dev) 0.9.5 or newer, Python 3 (written and tested on 3.14; 3.9 and later
parse it), `git`, the [GitHub CLI](https://cli.github.com) and [Claude Code](https://claude.com/claude-code).

```sh
git clone https://github.com/eriksalsborn/collinear ~/collinear
python3 ~/collinear/install.py
```

The installer copies the two popups and the prompts next to your herdr config, adds a key for each popup to your
`config.toml`, and reloads herdr. It only ever adds to your config (a copy is kept as `config.toml.before-collinear`),
and if `prefix+alt+i` or `prefix+alt+p` is already yours it asks for another key, or takes one up front:

```sh
python3 ~/collinear/install.py --issues-key prefix+alt+l --prs-key prefix+alt+g
```

Then open herdr and press the key. The first time, Col shows a checklist and says how to fix each line, most of
them right there in the popup:

| Line | Fixed by |
|---|---|
| your name | `N`, type it |
| Linear API key | `L`, paste it (it goes into the macOS keychain, nowhere else) |
| `gh` signed in | `G`, paste a token, or run `gh auth login` |
| `claude` on the PATH | install Claude Code and run it once |
| your repositories folder | `F`, type the folder your git repositories are cloned into |
| herdr version | `herdr update` if it is older than the version this was tested with |
| Linear and Slack connectors | claude.ai → settings → connectors, then `/mcp` in Claude Code |

You can skip the checklist with esc and come back with `D`. The list itself only needs the Linear key; the
connectors are for Col's answers and the daily report, and are found by name, so yours need not be called exactly
what the author's are.

To update later: `git -C ~/collinear pull && python3 ~/collinear/install.py`. Your settings and keys are left alone.

<details>
<summary>Installing by hand instead</summary>

Copy `issues.py`, `prs.py` and `prompts/` into `~/.config/herdr/`, copy `local_settings.example.json` there as
`local_settings.json`, add this to your `config.toml` (and the same for `prs.py`, on another key), then run
`herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+alt+i"
type = "popup"
command = "python3 ~/.config/herdr/issues.py"
description = "start work on a Linear issue"
width = "90%"
height = "80%"
```

</details>

## Good to know

- **`?` lists every key.** The footer shows what you can do on the screen you are on. Everything is also clickable,
  and the wheel scrolls.
- **Repositories are looked for in one folder,** the one you gave in setup (`F` there changes it). Starting an issue makes a git worktree next to the repository,
  at `<repo>-wt/<branch>`, on the branch name Linear gives the issue. Your main checkout is never touched.
- **The link between an issue and a workspace is the workspace's name.** A herdr workspace whose name starts with
  the issue id (`ENG-123 …`) belongs to that issue. Rename it and the link is gone; name one by hand and it is found.
- **Work happens in phases:** write the issue, plan, build, review. Each phase is a Claude Code tab in the issue's
  workspace and leaves notes in `~/.claude/work/<issue id>/`, so the next phase picks up where the last one stopped.
  The prompts are plain files in `prompts/`; edit them to change how a phase works.
- **Col is read-only.** His answers and the daily report come from a headless Claude with read access to Linear,
  Slack, GitHub and the web. He can post to Slack only when you ask him to, and a comment he drafts is never posted
  until you confirm.
- **Changes are instant and undoable.** Status, priority and assignee update on screen at once and are sent to
  Linear in the background. `U` undoes the last one.
- **Where things are kept:** cached issue lists and small settings in `~/.cache/herdr-issues*.json`, notes and chat
  threads in `~/.claude/work/`, the Linear key in the keychain as `linear-api-key`, and this machine's settings in
  `~/.config/herdr/local_settings.json`.
- **The PR popup can link a PR to its Linear issue** once `local_settings.json` has your `linear_workspace` (the
  `acme` of `https://linear.app/acme`) and `linear_team_keys` (your teams' issue prefixes, like `["ENG", "OPS"]`).
- **Selecting text** in the popup needs your terminal's modifier (usually Option or Shift while dragging), because
  the popup listens to the mouse.
- **`K`** hides the footer hints, **`Z`** sends Col off duty, **`B`** gives him a bone.
- **macOS only for now:** it uses the keychain, `open` and `pbcopy`.

## Status

A personal tool, shared as it is. It is used every day on one machine (macOS, herdr 0.9.5) and has not been tried
widely, so expect rough edges on a setup that differs from the author's. Issues and pull requests are welcome.

## Tests

```sh
python3 issues.py --test    # the pure helpers
python3 test_ui.py          # every screen, played against fake data and compared with ui_test_screens/
python3 test_ui.py --update # after a change to the UI that you meant to make
```

`test_ui.py` touches nothing real: no network, no processes, no keychain.

## How it works

```mermaid
flowchart TD
    you([You, in herdr]) -- "prefix + alt + i" --> popup

    subgraph popup["Collinear popup · issues.py"]
        list["Issue list<br/>state · drawing · one key table"]
        col["Col<br/>ask · draft a comment · daily report"]
    end

    list <-- "issues, status, comments<br/>(GraphQL, key from keychain)" --> linear[(Linear)]
    list -- "which workspaces are open,<br/>create, focus" --> herdr["herdr workspaces and tabs"]
    list -- "worktree on the issue's branch" --> git["git repositories<br/>in your repositories folder"]
    list -- "enter: pick a phase" --> prompt["prompt for the phase<br/>prompts/*.md + the issue"]

    prompt --> session["Claude Code in a new tab<br/>of the issue's workspace"]
    herdr --> session
    git --> session
    session <-- "handed from phase to phase" --> notes["notes<br/>~/.claude/work/&lt;issue id&gt;/"]

    col -- "headless Claude, read-only" --> sources["Linear · Slack · GitHub · web"]
    col --> notes

    you -- "prefix + alt + p" --> prs["PR popup · prs.py"]
    prs <--> github[(GitHub)]
```

One file does the work: `issues.py`. `IssueListScreen` holds the list's state, draws it, and runs one method per
key; `KEY_BINDINGS` is the single table the key handling, the `?` screen and the footer are read from. `prs.py` is
the separate PR popup and lends `issues.py` a few helpers.
