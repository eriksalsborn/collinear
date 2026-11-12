```
        ✦    ^ ▐▌ ^  C O L L I N E A R    ✦

                                  ╭────────────╮
                                  │  ◢▀▄▁▁▁▄▀◣ │
                         “woof” ‹ │ ▐ ^ ▐▌ ^ ▌ │
                                  │  ▜▖ ▼  ▗▛  │
                                  │   ╰─ω─╯~   │
                                  ╰──╴ Col ╶───╯
```

Collinear is a popup for [herdr](https://herdr.dev) that shows your Linear issues and turns one into a running
coding session in a single key press, so you never lose track of what you are working on, where its code is checked
out, or which workspace it lives in.

Col is the border collie in the corner. He herds the issues, writes your daily report, answers questions about an
issue, and accepts bones.

## Install

You need macOS, herdr, Python 3 (written and tested on 3.14; 3.9 and later parse it), `git`, the
[GitHub CLI](https://cli.github.com) and [Claude Code](https://claude.com/claude-code).

1. Get the code next to your herdr config. Do not copy this repo's `config.toml` over yours; it is the author's own.
   The repo is private, so you need to have been invited to it first.

   ```sh
   git clone https://github.com/eriksalsborn/collinear ~/collinear
   cp -R ~/collinear/issues.py ~/collinear/prs.py ~/collinear/prompts ~/.config/herdr/
   cp ~/collinear/local_settings.example.json ~/.config/herdr/local_settings.json
   ```

2. Open `~/.config/herdr/local_settings.json` and set:
   - `repositories_folder`: the folder your git repositories are cloned into (the example says `~/Development`).
   - `linear_workspace` and `linear_team_keys`: the `acme` of `https://linear.app/acme` and your teams' issue
     prefixes. Only the PR popup's "open Linear issue" action needs these.

3. Add the two popups to `~/.config/herdr/config.toml`:

   ```toml
   [[keys.command]]
   key = "prefix+alt+i"
   type = "popup"
   command = "python3 ~/.config/herdr/issues.py"
   description = "start work on a Linear issue"
   width = "90%"
   height = "80%"

   [[keys.command]]
   key = "prefix+alt+p"
   type = "popup"
   command = "python3 ~/.config/herdr/prs.py"
   description = "open PRs"
   width = "90%"
   height = "80%"
   ```

4. Reload herdr and open the popup with `prefix + alt + i`:

   ```sh
   herdr server reload-config
   ```

   The first time, Col walks you through a short checklist and says how to fix each line: your name, a Linear API
   key (pasted into the popup, stored in the macOS keychain, nowhere else), `gh` signed in, `claude` on the PATH,
   your repositories folder, and the Linear and Slack connectors in Claude Code (claude.ai → settings → connectors).
   You can skip it with esc and come back with `D`; the list itself only needs the Linear key.

## Good to know

- **`?` lists every key.** The footer shows what you can do on the screen you are on. Everything is also clickable,
  and the wheel scrolls.
- **Repositories are looked for in your `repositories_folder`.** Starting an issue makes a git worktree next to the repository,
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
  threads in `~/.claude/work/`, the Linear key in the keychain as `linear-api-key`.
- **Selecting text** in the popup needs your terminal's modifier (usually Option or Shift while dragging), because
  the popup listens to the mouse.
- **`K`** hides the footer hints, **`Z`** sends Col off duty, **`B`** gives him a bone.
- **macOS only for now:** it uses the keychain, `open` and `pbcopy`.

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
