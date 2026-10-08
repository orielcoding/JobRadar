# ארכיטקטורה

## עיקרון מרכזי: השלבים מדברים רק דרך מסד הנתונים

כל משרה נמצאת בסטטוס אחד. כל שלב לוקח משרות בסטטוס מסוים ומעביר אותן לסטטוס הבא. שלבים לא קוראים זה לזה. מזה נובעות כמה תכונות:

- **אפשר להחליף שלב** בלי לגעת באחרים, כל עוד הוא שומר על החוזה: איזה סטטוס הוא קורא ולאן הוא מעביר.
- **הרצה חלקית לא מאבדת כלום.** אם המכסה נגמרה באמצע, מה שלא טופל נשאר בסטטוס הממתין להרצה הבאה.
- **אפשר להריץ כל שלב לבד:** `jobradar run --stages triage,deep_eval`.

```
                 ┌──────────────┐
  ingest ──────▶ │     new      │
                 └──────┬───────┘
          dedupe        │──────────────────────────▶ duplicate
          hard_filter   │──────────────────────────▶ filtered_out
          enrich        │  (description fetched from a public ATS link; light_match ──▶ deep_pending)
                 ┌──────▼───────┐
                 │triage_pending│
                 └──────┬───────┘
          triage        │──────────────────────────▶ rejected_triage
                        │── no description ───────▶ light_match ──▶ (digest ping / `paste`)
                 ┌──────▼───────┐
                 │ deep_pending │ ◀── `paste` / `add`
                 └──────┬───────┘
          deep_eval     │  (3 failures ──▶ error)
                 ┌──────▼───────┐
                 │  evaluated   │  decision = notify | skip
                 └──────┬───────┘
          notify        ▼  notified_at is set (no repeat pings)
```

סדר השלבים מוגדר ב‑`config.yaml › pipeline.stages`.

**enrich:** משרות בלי תיאור (בעיקר מפיד techmap) שהקישור שלהן הוא דף ציבורי של Comeet, Lever או Greenhouse מקבלות את התיאור מה‑API הציבורי של אותה מערכת. כל משרה נבדקת פעם אחת (`extra.enrich_tried`). קישורי לינקדין לא נפתחים אף פעם.

**report:** כל הרצה מוסיפה לדוח היומי טבלאות בפורמט אחיד: לסקירה, נפסלו בהערכה המעמיקה, עברו את Haiku בלי תיאור, ונפלו ב‑Haiku. שורה אחת לכל משרה. הפירוט המלא של משרה: `jobradar show <id>`.

**מועדפות:** בשלב triage, משרה מחברה מועדפת עוברת ישר ל‑`deep_pending` (או ל‑`light_match` אם אין לה תיאור). ההחלטה על פינג משתמשת בספים של `favorites`.

**נטוורקינג:** הטבלאות לא משפיעות על הסטטוסים. השלבים notify ו‑report רק קוראים אותן כדי להוסיף ⭐ 🤝 📌 ולסדר את הפינגים: קשר חם מעלה משרה ברשימה.

## מבנה התיקיות

```
jobradar/
├── CLAUDE.md          instructions for the local agent (onboarding playbook, guardrails)
├── .claude/skills/    /onboard and /weekly for Claude Code
├── config/            *.example.yaml = templates; the real files are created by `init`
├── profile/           profile.md + cv.md (your inputs)
├── prompts/           LLM instructions, versioned; profile_interview.md is for claude.ai chat
├── docs/
├── src/jobradar/
│   ├── cli.py         all commands
│   ├── config.py      config + defaults + .env
│   ├── models.py      JobPosting, Status
│   ├── store.py       SQLite
│   ├── pipeline.py    runs stages, lock, logging
│   ├── sources/       one file per ATS + gmail_alerts + email_parsers + detect
│   ├── stages/        ingest, dedupe, hard_filter, enrich, triage, deep_eval, notify, report
│   ├── llm/           backend contract, claude_cli, fake, schemas
│   ├── notifiers.py   ntfy, telegram, email, console
│   ├── examples.py    turns your ratings into few-shot examples
│   ├── review.py      calibrate / review (terminal)
│   ├── evaluate.py    eval harness
│   ├── setup_status.py  derives setup progress from files + DB (for people and agents)
│   ├── network.py     contacts, interactions, applications; LinkedIn CSV import
│   ├── cli_network.py `net`, `apps`, `view` commands
│   ├── favorites.py   ⭐ companies (highlight, never exclusive)
│   ├── view.py        reports/network.html (local only)
│   ├── tracker.py     what you did with each surfaced job (to_review / to_apply / applied / dismissed), funnel 2/7/30d
│   ├── inbox_server.py + inbox.html   `jobradar inbox`: local window (127.0.0.1) with week calendar + one-key actions
│   └── scheduling.py  generates launchd / Task Scheduler / cron files
├── seeds/             techmap_companies.json (Israeli companies with ATS ids, ODbL)
├── tools/             build_techmap_seed.py (refreshes the seed)
├── tests/             offline tests + fixtures + fake `claude`
├── data/              (generated) jobradar.db, logs
└── reports/           (generated) daily reports
```

## החוזים (נקודות ההחלפה)

### Source (מקור משרות): `sources/__init__.py`
```python
class MySource:
    name: str
    def fetch(self) -> list[JobPosting]: ...
    # optional: prepare(store), after_ingest(store)
```
כדי להוסיף מערכת גיוס חדשה:
1. יוצרים קובץ `sources/myats.py` עם `@register("myats")` ומחלקה עם `__init__(self, company, config)` ו‑`fetch()`.
2. מוסיפים את ה‑import ב‑`_load_builtin()`.
3. מוסיפים תבנית זיהוי ב‑`detect.py`.
4. מוסיפים fixture ובדיקה.

### Stage (שלב): `stages/__init__.py`
```python
class MyStage:
    name = "my_stage"
    def run(self, ctx) -> dict:  # stats
        for job in ctx.store.jobs_by_status(Status.X): ...
```
רושמים ב‑`STAGES` ומוסיפים ל‑`pipeline.stages`.

### LLM backend (מודל): `llm/__init__.py`
```python
def complete_json(self, *, system, user, schema, model, purpose, context) -> LLMResult
```
- מעלים `UsageLimitReached` כשהמכסה נגמרה. השלבים עוצרים ומשאירים עבודה להרצה הבאה.
- מעלים `LLMError` על כל כשל אחר. המשרה תנוסה שוב, עד 3 פעמים.

כדי להוסיף backend (למשל מודל מקומי דרך Ollama לסינון המהיר), מממשים את הפונקציה ורושמים ב‑`get_backend`. כרגע אותו backend משמש את שני השלבים. פיצול לפי שלב מתואר ב‑ROADMAP.

### Notifier (ערוץ התראה): `notifiers.py`
```python
def send(self, title, body, url=None, priority=3) -> None
```

## איך מתבצעת קריאה ל‑Claude (בלי API)

`llm/claude_cli.py` מריץ:
```
claude -p "<task>" --output-format json --model <haiku|sonnet>
       --system-prompt-file <prompt> --json-schema <schema>
       --tools "" --strict-mcp-config --disallowedTools "mcp__*"
       --no-session-persistence --setting-sources "" --max-turns 4
```
- נתוני המשרה והפרופיל עוברים ב‑stdin. התשובה מגיעה ב‑`structured_output`. אם אין שם תשובה, הקוד קורא את ה‑JSON מתוך הטקסט.
- הקריאה רצה בתיקייה ריקה **מחוץ לפרויקט** (בתיקיית ה‑temp של המערכת), בלי כלים, בלי MCP ועם `--setting-sources ""`. כך לא נטענים ההגדרות האישיות שלך ב‑Claude Code וגם לא `CLAUDE.md` של הפרויקט (שנכתב בשביל הסוכן שעובד איתך, לא בשביל ההערכות), והמודל לא יכול לגעת בקבצים.
- נבדק בפועל: עם `--setting-sources project` קובץ `CLAUDE.md` בתיקיית אב **כן** מגיע למודל. עם `""` הוא לא מגיע.
- `ANTHROPIC_API_KEY` מוסר מהסביבה בכוונה, כדי שהחיוב תמיד יהיה על המנוי.
- נבדק מול Claude Code 2.1.286.

## מודל הנתונים (SQLite)

| טבלה | תוכן |
|---|---|
| `jobs` | משרה לכל שורה: מקור, חברה, כותרת, תיאור, `status`, `decision`, `notified_at` |
| `evaluations` | כל תשובת מודל (triage / deep / eval), עם מודל, גרסת פרומפט וזמן ריצה |
| `labels` | הדירוגים שלך: good/ok/bad, הערה ומקור (calibrate/review) |
| `runs` | הרצה לכל שורה, עם סטטיסטיקה של כל שלב |
| `seen_emails` | מיילים שכבר נקראו |
| `contacts` | אנשי קשר: חברה (מנורמלת לצורך התאמה), תפקיד, סוג וחוזק קשר, מקור (linkedin_csv / manual) |
| `interactions` | שיחות, עם תאריך תזכורת ו‑done. מקושרות לאיש קשר ואופציונלית לתהליך |
| `applications` | תהליכי הגשה: `job_id` אופציונלי, סטטוס, מי הפנה, הצעד הבא |
| `app_events` | היסטוריית סטטוסים לכל תהליך |
| `job_tracking` | המעקב: לכל משרה שהוצגה לך: סטטוס (לעיון / להגשה / הוגש / נדחה), מתי הופיעה, מתי עיינת, מתי הגשת, וסיבת דחייה. לא חלק מה-pipeline: נוצר בעצלות (`Tracker.sync`) מתוך הסטטוסים |
| `meta` | גרסת סכמה (לצורך מיגרציות בעתיד) |

אפשר לפתוח את הקובץ בכל כלי SQLite, למשל DB Browser for SQLite.

## בדיקות

```bash
python -m unittest discover -s tests -v
```
הבדיקות לא צריכות רשת או מודל. יש fixtures לכל מערכת גיוס ולמיילי לינקדין, `claude` מזויף שבודק את הדגלים ואת הטיפול בשגיאות, והרצה מלאה של השרשרת עם backend מזויף.
