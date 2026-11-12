Phase: REVIEW. You didn't write this code; review it with fresh eyes.
In each worktree, diff the branch against its default branch (git merge-base origin/HEAD). Check it against the Spec and the risks in plan.md: correctness bugs, spec items missed or done differently, risks not handled, missing tests, anything that would surprise a reviewer.
Report findings ranked by severity, each with file:line and why it matters. Don't fix anything yet; wait for me to say which to fix.
When we're done, write {notes}/review.md with the findings and what was fixed. Then offer to open a PR.
