Phase: BUILD. Implement the Spec in plan.md.

First, judge whether this is very simple: a few lines in one or two files, one obvious way to do it, nothing risky (no data, infra, auth, money or public interfaces touched). Tell me your verdict in one line and wait for my go.
- Very simple: implement it in one go, run tests and lint, then show me the diff summary.
- Otherwise I'm in the loop. Split the work into small steps and show me the list. Then for each step: say what you'll change and why, wait for my go, make the change, run the relevant tests, show what changed, and wait again before the next step.

Either way: if reality differs from the spec, stop and ask instead of improvising.
When done, write {notes}/build.md: what was built and any deviations from the spec. If you deviated, update the Agent notes section of the issue description to match.
