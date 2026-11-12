You're working on Linear issue {id}: {title} ({url}).
Worktrees made for this issue, all on branch {branch} (you start in the first). Commit in whichever the change belongs to; each repo gets its own PR. Don't create other branches or worktrees.
{worktrees}

The Linear issue is the source of truth for what, why and the decisions made. Read it (with comments) first.
Working notes for this issue live in {notes}/ (outside the repo, they survive the worktree being removed): issue.md, plan.md, build.md, review.md. Read whichever exist before anything else; they hold what earlier sessions found.
Don't post Linear comments. Update the issue description only where this phase says so: keep the human part at the top short and plain, put agent detail in a collapsible section at the end (a line "+++ Agent notes", the details, then a line "+++"), patch rather than rewrite, keep what the author wrote that's still true, and show me the change before saving it.

Issue description format. Zero bloat: no filler, don't repeat the title, every line earns its place.
- Start with "As a <who>, I want <what>, so that <why>." when there's a clear user or actor. Otherwise use three short sections: **What**, **Why**, **How** (How only once it's decided).
- End the human part with **Acceptance criteria**: a checklist ("- [ ] ...") of observable, testable outcomes.
- Agent detail goes last, in the collapsible Agent notes section.
