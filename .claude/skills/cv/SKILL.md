---
name: cv
description: "Build a tailored one-page CV for one job from the master CV and profile, asking the user questions where needed. Use when the user types /cv <job id or link>, or asks to prepare / tailor a CV for a specific job."
---

The user wants a CV tailored to one job. Speak Hebrew with them; the CV itself is in English unless they ask otherwise.

1. Read `prompts/cv_tailor.md` (in a fresh copy: `prompts/cv_tailor.example.md`) and follow it exactly. It is the whole method: inputs, gap check, questions, assembly rules, quality checks and outputs.
2. The job is the argument: a job id (`jobradar show <id>` prints the posting and its evaluation, no quota) or a link. Never fetch LinkedIn; ask the user to paste the description instead.
3. **A dashboard CV may already exist.** The dashboard's "צור קו״ח" button runs the same method headless (`jobradar cv <id>`, see `src/jobradar/cv_builder.py`). It answers the questions with safe defaults.
   - Look in `profile/tailored/work/` for `*_<id>_*.json`. It holds `open_questions` and `master_cv_proposals`.
   - If one exists, start from its `.md` and `.note.md`. Ask its open questions instead of starting over.
4. Truthfulness is a hard rule: every line comes from `profile/cv.md` or is confirmed by the user in this conversation. Propose master-CV additions; apply them only after the user approves.
5. Save the working files in `profile/tailored/work/` as the method says. The PDF comes from `jobradar cv-pdf <base>`, and it lands in `profile/tailored/`.
6. Tell the user in 2–3 lines what you chose and what the main remaining risk is.

Do not run `jobradar run`, `eval`, `paste`, `add` or `cv` without asking: they spend the user's quota. `jobradar cv-pdf` is free.
