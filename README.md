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
git clone --branch stable https://github.com/eriksalsborn/collinear ~/collinear
python3 ~/collinear/install.py
```

Then press `prefix + alt + i` in herdr. The first time, Col shows a checklist and fixes most of it with you right
there (Linear key, repositories folder, and so on). Then he takes you on [the tour](#the-tour).

If that key is already yours: `python3 ~/collinear/install.py --issues-key prefix+alt+l`.
To update: `git -C ~/collinear pull && python3 ~/collinear/install.py`.

`stable` is the latest [release](https://github.com/eriksalsborn/collinear/releases) and only moves when a new one is
published; `main` is work in progress. Cloned `main` earlier? `git -C ~/collinear switch stable`.

## The tour

Right after setup, Col walks you through the app for real: he says each step from his speech bubble and you press
the key, so the preview, the comments, The Daily Bark, the to-do list and the start dialog actually open in front
of you.

- It covers moving around, reading and writing comments, asking Col, changing status / priority / assignee,
  the daily report, the to-do list, the green markers and `w`, the active workspaces, and what starting work does.
  Free sessions, team and project views, the review inbox, the filter and new issues get a line each.
- Only the key he asks for works while it runs, so you can't get lost or post anything by accident.
- `Q` quits it at any time. `O` starts it again whenever you want.

## Good to know

- **One key from issue to working session.** `enter` on an issue sets up what you would otherwise do by hand:
  - a herdr workspace named after the issue (or a new tab in the one already open for it),
  - a git worktree on the issue's branch, in the repo you pick, with your main checkout left alone,
  - Claude Code opened there, pointed at the issue, its comments and your notes from earlier sessions,
  - a starting prompt: write it, plan it, build it or review it. Or no prompt, and you steer.

  Topics in the daily report and the to-do list start a session the same way, with the topic as the context.
- **It knows where your work is.** Issues with a workspace open are marked, with repository and branch, and `w`
  jumps straight there. The popup opens on just those, the active workspaces; `W` or `h` shows all your issues.
  With nothing open it opens on your issues.
- **Col writes your daily report.** A short update on your last working day, then the loose threads from Slack,
  Linear comments and pull requests: what nobody followed up on, or has no issue yet. `s` moves one to a to-do list
  that outlives the next report, and `i` there adds your own: typed, or drafted by Col from a few words.

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
    list -- "enter: pick a starting prompt" --> prompt["starting prompt<br/>prompts/*.md + the issue"]

    prompt --> session["Claude Code in a new tab<br/>of the issue's workspace"]
    herdr --> session
    git --> session
    session <-- "handed from session to session" --> notes["notes<br/>~/.claude/work/&lt;issue id&gt;/"]

    col -- "headless Claude, read-only" --> sources["Linear · Slack · GitHub · web"]
    col --> notes

    you -- "prefix + alt + p" --> prs["PR popup · prs.py"]
    prs <--> github[(GitHub)]
```

## Tests

```sh
python3 issues.py --test && python3 test_ui.py
```

## Keys

Everything is also clickable, and `?` shows this list in the app.

| | |
|---|---|
| **Work on an issue** | `enter` start · `space` preview · `w` jump to its workspace · `a` ask Col · `C` comment · `c` free session |
| **Change it** | `S` status · `P` priority · `A` assign · `X` cancel · `U` undo · `n` new issue |
| **Open elsewhere** | `g` pull request · `L` Linear · `y` copy link |
| **Views** | `;` next tab · `h` previous tab (my issues, active workspaces, daily bark, to-do) · `b` daily bark · `p` to-do list · `W` active workspaces · `t` team · `T` project · `R` review inbox · `r` refresh |
| **Move** | `j` / `k` · `5j` · `G` last · `/` or just type to filter |
| **The rest** | `O` the tour · `D` setup · `K` hints on/off · `Z` Col on/off · `B` give Col a bone · `h` back · `q` close |

## License

[MIT](LICENSE)
