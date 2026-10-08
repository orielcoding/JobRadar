---
name: weekly
description: "JobRadar weekly routine. Go over follow-ups and open applications, rate the jobs the user was pinged about, check run health, and suggest one improvement. Use when the user types /weekly or asks to review recent matches."
---

Weekly JobRadar routine. Speak Hebrew with the user. Keep each part short and let them skip parts.

1. **Follow-ups and applications.**
   - Run `jobradar net followups --json` and `jobradar apps list --json`.
   - List due or overdue follow-ups, and applications whose `next_step_on` has passed or that haven't changed in 10+ days.
   - For each one, ask what happened and record it with `net log` / `net done` / `apps update`, following "Networking and applications" in CLAUDE.md.
   - Offer `ghosted` for applications with no response for 3+ weeks.
2. **Run health.** Run `jobradar stats`. Report briefly:
   - new jobs and pings since last week
   - any source that failed repeatedly (check `data/logs/jobradar.log`)
   - whether runs were cut short by the usage limit
3. **Rate the pings.** Run `jobradar list --for review --json` and go over the jobs 3–5 at a time:
   - For each job, show the title, company, the model's scores and pitch, and who the user knows there (`jobradar net at <company>`).
   - Ask for good / ok / bad plus one sentence of "why".
   - Save each answer with `jobradar label <id> <label> --note "<their words>"`. Record their judgment, not yours.
   - If they want to apply to a job where they know someone, suggest asking for a referral first. Open the application with `apps add --job <id>`.
4. If the user disagreed with the model on several jobs, name the pattern in one or two sentences.
5. Propose exactly one improvement, following "Improving match quality" in CLAUDE.md:
   - usually a concrete edit to `profile/profile.md`
   - otherwise a filter or threshold change, verified with `jobradar filter-test`
   - otherwise a prompt change, verified with `jobradar eval`

   Make the change only after the user agrees.
6. If they have 30+ ratings and haven't measured recently, offer `jobradar eval --limit 20`. It spends Pro quota, so ask first.
7. Run `jobradar view` and tell them `reports/network.html` is updated. It is a local file; never publish it.
