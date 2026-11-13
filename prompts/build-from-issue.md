Phase: BUILD FROM THE ISSUE. There is no plan.md and no Spec: the issue is the spec. Build what it asks for.

First read the affected code, without changing it. Then tell me in a few lines: what you'll build, the acceptance criteria you'll hold it to (the issue's own, or the ones you propose when it has none), and anything the issue leaves undecided. Ask me about what is undecided, one question at a time, and wait for my go.
If it turns out bigger or riskier than the issue suggests (data, infra, auth, money or public interfaces touched, or several reasonable ways to do it), stop and say so: it wants a plan first.

Then judge whether this is very simple: a few lines in one or two files, one obvious way to do it, nothing risky.
- Very simple: implement it in one go, run tests and lint, then show me the diff summary.
- Otherwise I'm in the loop. Split the work into small steps and show me the list. Then for each step: say what you'll change and why, wait for my go, make the change, run the relevant tests, show what changed, and wait again before the next step.

When done, write {notes}/build.md: what was built, the acceptance criteria it was held to, and the decisions made along the way and why. Put those decisions in the Agent notes section of the issue description.

Then offer to open a draft pull request, one for each repo that changed, and wait for my answer. On yes: if a skill or command for creating draft pull requests is available to you, use it. Otherwise commit what is left, push the branch and run `gh pr create --draft`, with a title that starts with {id} and a short description of what changed and why, linking the issue ({url}). Give me the link to each one.
