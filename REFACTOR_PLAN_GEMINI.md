# Plan: wider coverage of large-company jobs

Status as of 2026-10-08. The original proposal (a full ingest refactor, Google dorking, company tiers, 3-level dedupe) was reviewed against the code and the DB, and replaced by the smaller plan below.

## What the data showed

- techmap already lists the large companies' Israeli jobs (about 370 in one week: Nvidia 169, Mobileye 68, Google 43, ...). Every one of those links points to LinkedIn, so `enrich` cannot add a description and they stay light matches.
- techmap reports jobs late: 3.5 days after posting at the median, and 65% are older than 2 days when they arrive. With `max_age_days: 2`, most of them were dropped as "too old".
- Most of the remaining drops are the user's own rules, such as no Senior. That is intended: Senior stays excluded.

## Done

1. **Age from first sight for late sources.** `filters.late_sources: [techmap]`. For these sources, age counts from `first_seen_at`, except on the source's first fetch, so there is no flood. One helper, `hard_filter.job_age_days`, is used by `hard_filter`, the `dedupe` window and the `notify` ping age.
2. **Workday source** (`sources/workday.py`, `ats: workday`, `detect` support, fixture and test).
   - Nvidia, Intel, KLA and Applied Materials are wired in.
   - It filters by country through Workday's own location facet.
   - Descriptions are fetched only for new postings that are not already too old.
   - `dedupe` replaces the description-less LinkedIn/techmap copy with the Workday copy.
3. **TLS fallback.** `http.py` retries with `certifi` when the OS certificate store fails verification. This happened on Windows, where an expired ISRG Root X2 broke every Let's Encrypt site.

## Not doing (and why)

- **Rewriting sources into an `ingest/adapters/` package.** `sources/` with `@register` already is that pattern.
- **Google dorking / search-engine discovery.** It is scraping, gets blocked by CAPTCHAs, and breaks the project's no-scraping rule.
- **Company tiers.** `favorite: true` already does this.
- **Tier-based looser filters.** They override the user's explicit choices.
- **3-level dedupe.** LinkedIn alerts carry no requisition id. The ROADMAP trigger ("same job pinged twice") has not appeared.

## Next, only if needed

- **Amazon** (`amazon.jobs` search JSON) and **Microsoft** (careers search JSON): one dedicated source each. Low volume of non-senior roles in Israel, so only if real misses show up.
- **Apple, Google, Meta:** their sites are internal and fragile, and Meta blocks bots. These are not planned. Use techmap titles plus `paste`, or later a "send to radar" browser button (ROADMAP).
- **Qualcomm and Mobileye:** not on Workday. Check their ATS with `jobradar detect` first.
