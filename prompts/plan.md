Phase: PLAN. Three stages in this one session. Finish each stage, then stop and wait for me to say "next".

1. THINK. Read the issue via Linear, including comments, the parent issue, related issues and any screenshots. Don't look at code yet and don't propose solutions. Grill me, one question at a time, so that I do the thinking: what is this really and why now, what could it break, what are the risks, which solutions are possible, and what's missing or unclear in the issue. Push back on vague answers. Start with your first question and wait.
   When I say next: write the "Think" section of {notes}/plan.md (what and why, risks, options, open questions) and update the top of the issue description if the what, why or risks got clearer.

2. DISCOVER. Find out how things actually work today: the affected code, callers, data, infra, prior art and gotchas. Check the risks and open questions from Think against reality. Don't change code. Report what you found and what it means for the options.
   When I say next: write the "Discovery" section of plan.md.

3. SPEC. Ask me about anything still undecided, one question at a time. Then draft the "Spec" section of plan.md: goal, non-goals, chosen approach and why, changes per file or component, risks and mitigations, test plan, rollout and rollback. Revise until I approve.
   When approved: put the decision (chosen approach and why, in a few lines) in the human part of the issue description, and the spec in the Agent notes section.
