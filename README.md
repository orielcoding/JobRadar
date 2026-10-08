# JobRadar · רדאר משרות אישי

כלי שרץ כל בוקר על המחשב שלך. הוא אוסף משרות חדשות מאתרי הקריירה של חברות (כולל Google, Microsoft, Nvidia ו-Intel) ומהתראות מייל (כולל לינקדין), מסנן אותן ומעריך כל משרה מול הפרופיל שלך בעזרת Claude. כשיש משהו שבאמת מתאים, נשלח פינג לטלפון.

המודל רץ דרך **Claude Code המקומי שלך על מנוי הפרו** (`claude -p`). אין API, אין עלות נוספת ואין שרת.

```
 SOURCES               CODE ONLY (free)                      CLAUDE (your Pro quota)                  OUTPUT
 Greenhouse ─┐
 Lever       ├─▶ ingest ─▶ dedupe ─▶ hard_filter ─▶ triage ──────────▶ deep_eval ──────────▶ notify ─▶ phone ping
 Ashby       │                       location,      haiku,              sonnet,                 report ─▶ daily .md
 Workable    │                       title, age     25 jobs / call      1 job / call
 Comeet      │                                                            ▲
 Workday     │ (Nvidia, Intel, KLA, AMAT)
 Eightfold   │ (Microsoft, Qualcomm)
 Google      │ (Google Careers)
 techmap ────┤ (daily Israeli job feed by role)       your ratings: good / ok / bad + "why"
 Gmail ──────┘ (LinkedIn / AllJobs alerts)
                                                     your network: contacts · conversations · applications
```

## מה מקבלים

- **פינג** לכל משרה ששווה להגיש אליה, עם החלטה (**להגיש עכשיו / להגיש / סיכוי נמוך / לא רלוונטי**) ו**שורת נימוק אחת**. המודל שואל "האם שווה להגיש?" ולא "האם יקבלו אותי?", כי הגשה זולה. לצד ההחלטה מופיעים **רצון** (האם תרצה את המשרה) ו**מעבר סינון** (האם מגייס יעביר את הקו״ח הטוב ביותר שאפשר להרכיב מקו״ח האב שלך). כשהקו״ח מסתיר את ההתאמה, הפינג מסמן ✏️: שווה להתאים את הקו״ח למשרה.
- **דוח יומי** (`reports/YYYY-MM-DD.md`): בכל הרצה, טבלאות בפורמט אחיד. משרות לסקירה, משרות שנפסלו בהערכה המעמיקה, משרות שעברו סינון ראשוני בלי תיאור, ומשרות שנפלו בסינון הראשוני. שורה לכל משרה, עם ציונים, "למה כן" ו"למה לא". הפירוט המלא של משרה: `jobradar show <id>`.
- **קו״ח מותאם למשרה** לפי דרישה: `/cv <מספר משרה או קישור>` בונה עמוד אחד מקו״ח האב, לפי `prompts/cv_tailor.md`.
- **פינג מרוכז** על משרות שאין להן תיאור (מלינקדין או מפיד techmap). מעבירים את התיאור עם `jobradar paste <id>` ומקבלים הערכה מלאה.
- **נטוורקינג ותהליכי הגשה באותו מקום:** אנשי קשר (ייבוא מלינקדין), שיחות ותזכורות, ותהליכי הגשה שמקושרים למשרה ולמי שהפנה. כל פינג מציג ⭐ לחברה מועדפת, 🤝 מי אתה מכיר שם ו‑📌 אם כבר יש תהליך. הרישום עצמו נעשה במשפט רגיל לסוכן.

## הדרך הקלה: סוכן מקומי שמוביל אותך

התיקייה כוללת `CLAUDE.md` עם הוראות מלאות לסוכן. פותחים את התיקייה ב‑Claude Code: בטרמינל `cd jobradar` ואז `claude`, או דרך לשונית Code באפליקציית Claude. אחר כך כותבים:

```
/onboard
```

הסוכן בודק איפה אתה עומד (`jobradar setup-status`) ומבקש ממך רק את מה שחסר, צעד אחר צעד. הוא גם מעביר את ראיון הפרופיל ואת הכיול בשיחה.

פקודות שוטפות בסוכן:
- `/log דיברתי עם...`: רישום שיחה או עדכון הגשה
- `/weekly`: תזכורות, תהליכים ודירוג פינגים

## התחלה ידנית

את כל ההוראות, לפי הסדר, אפשר למצוא ב‑[docs/SETUP.md](docs/SETUP.md). בקיצור:

```bash
cd jobradar
python3 -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
jobradar init                 # יוצר קבצי הגדרות ו-.env
# ממלאים פרופיל, קו״ח, חברות ומסננים (ראה SETUP)
jobradar doctor --ping        # בודק שהכל מחובר ושולח פינג ניסיון
jobradar fetch                # משיכה ראשונה, בלי מודל
jobradar calibrate            # דירוג כ-25 משרות (מלמד את הטעם שלך)
jobradar run                  # הרצה מלאה
jobradar schedule macos       # או windows / linux: תזמון יומי
```

## פקודות

| פקודה | מה היא עושה |
|---|---|
| `init` | יצירת קבצי הגדרות מהתבניות, וקובץ `.env` עם נושא ntfy פרטי |
| `doctor [--ping]` | בדיקת התקנה: קבצים, Claude Code, Gmail והתראות |
| `detect <url>... [--add]` | זיהוי מערכת הגיוס מקישור לדף קריירה והוספה לרשימה |
| `test-sources [--only X]` | משיכה ניסיונית מכל מקור, בלי לשמור |
| `fetch` | משיכה, איחוד כפילויות ומסננים, בלי מודל |
| `run [--dry-run] [--max-deep N]` | הרצה מלאה. זו הפקודה שהמתזמן מריץ |
| `calibrate [-n 25]` | דירוג מדגם משרות. עושים פעם אחת בהתקנה |
| `review` | דירוג המשרות שקיבלת עליהן פינג. כדאי פעם בשבוע |
| `show <id>` | הצגת משרה וההערכה שלה |
| `paste <id>` | הוספת תיאור למשרה בלי תיאור (למשל מלינקדין) והערכה מיידית |
| `add --title --company [--url]` | הוספת משרה ידנית והערכה שלה |
| `eval [--limit 20]` | מדידה: כמה המודל מסכים עם הדירוגים שלך |
| `stats` | מצב המערכת וההרצות האחרונות |
| `setup-status [--json]` | איפה אתה בהקמה ומה השלב הבא (בלי מודל ובלי רשת) |
| `list --for calibrate\|review [--json]` | משרות לדירוג. משמש את הסוכן לכיול בשיחה |
| `label <id> good\|ok\|bad --note "..."` | שמירת דירוג בלי ממשק אינטראקטיבי |
| `filter-test [--apply]` | מה כל מסנן פוסל, על המשרות שכבר נמשכו |
| `import-techmap [--sizes --industries]` | הוספת כ‑180 חברות ישראליות עם מערכת גיוס ידועה |
| `jobs <טקסט>` | חיפוש משרה ב‑DB (כדי למצוא id) |
| `net ...` | אנשי קשר, שיחות ותזכורות (פירוט ב‑NETWORKING.md) |
| `apps ...` | תהליכי הגשה |
| `view` | עמוד מקומי: תזכורות, תהליכים וחברות שבהן אתה מכיר מישהו |
| `schedule macos\|windows\|linux` | יצירת קובץ תזמון יומי עם הנתיבים שלך |

## מסמכים

- [docs/SETUP.md](docs/SETUP.md): מה אתה צריך לספק, איפה ובאיזה סדר. כולל פעולות ידניות ופתרון תקלות.
- [docs/OPERATING.md](docs/OPERATING.md): תפעול שוטף. מה רץ לבד, מה כל פינג אומר, ומה באחריותך ומתי.
- [docs/MATCHING.md](docs/MATCHING.md): איך עובדת הערכת ההתאמה, איך משפרים אותה, ומילון מונחים.
- [docs/NETWORKING.md](docs/NETWORKING.md): אנשי קשר, שיחות ותהליכי הגשה, וייבוא מלינקדין.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): המבנה, החוזים בין החלקים, ואיך מוסיפים מקור, ערוץ או מודל.
- [docs/ROADMAP.md](docs/ROADMAP.md): איפה יש היום פתרון פשוט, למה הוא יוחלף ומתי.

נתוני חברות ומשרות ישראליות: [Israeli Tech Map](https://github.com/mluggy/techmap) מאת Michael Lugassy, ברישיון ODbL.
