<p align="center">
  <img src="assets/banner.svg" alt="COLLINEAR — Linear issues inside herdr, herded by Col the border collie" width="100%">
</p>

Collinear is a popup for [herdr](https://herdr.dev) that shows your Linear issues and turns one into a running
coding session in a single key press, so you never lose track of what you are working on, where its code is checked
out, or which workspace it lives in.

## Install

Needs macOS, herdr 0.9.5+, Python 3, `git`, the [GitHub CLI](https://cli.github.com) and
[Claude Code](https://claude.com/claude-code).

```sh
git clone https://github.com/eriksalsborn/collinear ~/collinear
python3 ~/collinear/install.py
```

Then press `prefix + alt + i` in herdr. The first time, Col shows a checklist and fixes most of it with you right
there (Linear key, repositories folder, and so on).

If that key is already yours: `python3 ~/collinear/install.py --issues-key prefix+alt+l`.
To update: `git -C ~/collinear pull && python3 ~/collinear/install.py`.

## Good to know

- `?` lists every key. Everything is also clickable.
- Starting an issue makes a git worktree at `<repo>-wt/<branch>`; your main checkout is never touched.
- A herdr workspace belongs to an issue when its name starts with the issue id (`ENG-123 …`).
- Col only reads (Linear, Slack, GitHub, the web). Nothing is posted until you confirm.
- `D` reopens setup, `U` undoes the last change, `B` gives Col a bone.

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

## Tests

```sh
python3 issues.py --test && python3 test_ui.py
```

## License

[MIT](LICENSE)
