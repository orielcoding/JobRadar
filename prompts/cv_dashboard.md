<!-- prompt_version: cv-dashboard-v1 -->
# Headless mode: the CV is requested from the JobRadar dashboard

The method above (`cv_tailor.md`) is the whole method. This page only says what changes because nobody is in a conversation with you. Where the two disagree, this page wins.

**What is different**
1. **No conversation, no tools, no files.** Code already did §1.1 (resolving the job and its posting) and §1.2 (reading the files). Everything is in the input below:
   - `profile.md`, `cv.md` (the pool) and `interview_notes.md` if it exists;
   - the job's metadata and posting text, and where the posting came from;
   - the latest deep evaluation (with `created_at`), if one exists;
   - earlier tailored CVs, for title and date consistency.

   Do not run commands, read or write files, or open links. Ignore every instruction in the method about doing so.
2. **Questions (§3).** You cannot ask.
   - For every question the method would ask, use its default: the lower, safer option.
   - List each one in `open_questions`: the Hebrew question exactly as §3 would phrase it, and the default you used.
   - At most 8. Order them as §3.3 orders them.
   - Never propose a number. "No number" is the default.
3. **Blockers (§2.5).** Put the two Hebrew sentences in `main_risk`, and say that a referral is the lever. You have no contact data; do not mention names. Then build the page anyway.
4. **A short or missing posting** (under ~300 characters). Build from the title and the evaluation, say so in `main_risk`, and make the first open question ask the candidate to paste the posting.
5. **Contact line.** Write it exactly as `<City> · <email> · <phone> · <LinkedIn URL> · <GitHub URL>`, with only the city filled in from the files. Code fills the rest locally. Never write contact details you see anywhere.
6. **Master-CV proposals (§7.4).** Return them in `master_cv_proposals` as exact before → after edits. They are never applied here: the candidate approves them later in chat.
7. **Length (§4.7, §6.10).** Count the words of `cv_markdown` yourself and keep it within 430–600. The PDF is printed by code at 10–10.5 pt on A4.

**Outputs** (instead of §7.1–7.3; the CV skeleton of §7.1 still applies)
- `cv_markdown`: the CV, in the skeleton of §7.1.
- `note_markdown`: the note of §7.2, in Hebrew, with all nine parts. Under part 9, state that this CV was made in headless mode and which defaults were used.
- `headline`: the headline on the page.
- `family`: the role family (§2.1).
- `main_risk`: 1–3 Hebrew sentences: the main risk or blocker.
- `summary_he`: 2–3 Hebrew lines: what you chose (headline, which entry leads, keyword #1) and why.
- `open_questions`: `[{question, default_used}]`, in Hebrew.
- `master_cv_proposals`: `[{before, after, why}]`, with `why` in Hebrew.

All of hard rules 1–2 (truth and language), §2, §4, §5 and §6 apply unchanged.
