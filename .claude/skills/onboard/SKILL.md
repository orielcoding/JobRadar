---
name: onboard
description: "Walk the user through JobRadar setup step by step, from wherever they are now, asking for everything that is needed. Use when the user types /onboard or asks to set up, continue setting up, or check what is missing."
---

Run JobRadar onboarding. Speak Hebrew with the user.

1. Make sure the venv exists and is active. If it doesn't exist, create it and run `pip install -e .`.
2. Run `jobradar setup-status --json`.
3. Tell the user, in two sentences at most, what is already done and what the next step is.
4. Work through the remaining steps in order, using the "Onboarding playbook" section of CLAUDE.md for the step id named in `next_step`. For each step:
   - ask only for what that step needs, and say why
   - do the work
   - verify it with the command the playbook names
   - re-run `jobradar setup-status --json` and move to the next step
5. Optional steps (`required: false`): offer each one in one sentence, and skip it if the user declines.
6. Stop and summarize when `complete` is true, or when the user wants to pause. The summary says what was done, what is left, and that `/onboard` continues from there next time.

Never print secret values from `.env`; the user pastes secrets there themselves. Ask before running anything that spends Pro quota (`run`, `eval`).
