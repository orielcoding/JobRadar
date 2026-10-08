---
name: log
description: "Record a networking event or application update from one plain sentence (who, where, what happened, what's next). Use when the user types /log or describes a conversation, a referral, an application or an interview."
---

The user just described a networking event or an application update. Speak Hebrew.

Follow "Networking and applications" in CLAUDE.md:

1. Parse the sentence into these parts:
   - person (name, company, role, relationship)
   - channel
   - date
   - what happened, as one line in the user's own words
   - follow-up date, if any ("remind me" with no date means +7; convert relative dates using today's date from the shell)
   - application change, if any
2. Run `jobradar net find <name> --json` and `jobradar net at <company> --json`. Never create a duplicate. If several people match, ask which one.
3. Run the minimal commands:
   - `net add` or `net update` for the person
   - `net log` for the conversation
   - `jobradar jobs <text> --json`, then `apps add` or `apps update`, when an application is involved
4. Reply in 1–2 lines with what was recorded and the follow-up date. Ask only about facts that are genuinely missing and would change what is stored. Never ask for phone numbers.

If the sentence mentions a company where there is a strong match (`jobradar jobs <company> --json` shows decision=notify) and no application yet, mention it in one line.
