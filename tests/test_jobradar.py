"""Offline tests. Run from the project root:   python -m unittest discover -s tests -v"""

from __future__ import annotations

import email
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIX = ROOT / "tests" / "fixtures"

from jobradar.config import Config  # noqa: E402
from jobradar.models import Status  # noqa: E402
from jobradar.sources import SOURCE_TYPES, _load_builtin  # noqa: E402
from jobradar.sources.detect import match_text  # noqa: E402
from jobradar.sources.email_parsers import parse_linkedin  # noqa: E402
from jobradar.store import Store  # noqa: E402

_load_builtin()


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def make_home(extra_config: str = "") -> Path:
    home = Path(tempfile.mkdtemp(prefix="jobradar-test-"))
    shutil.copytree(ROOT / "prompts", home / "prompts")
    (home / "config").mkdir()
    # Tests use their own small, stable filter set (the example file encodes the user's choices).
    (home / "config" / "filters.yaml").write_text(
        "locations:\n  allow_any_of: [israel, tel aviv, remote]\n"
        "title:\n  exclude_any: ['\\bintern\\b']\nmax_age_days: 45\n", encoding="utf-8")
    (home / "config" / "config.yaml").write_text(
        "llm:\n  backend: fake\n  fake_keywords: [data, engineer, python]\n"
        "notify:\n  channels: [console]\n" + extra_config, encoding="utf-8")
    (home / "config" / "companies.yaml").write_text("companies: []\n", encoding="utf-8")
    (home / "profile").mkdir()
    (home / "profile" / "profile.md").write_text("# Candidate profile\nData engineer, Python, Spark. " * 20)
    (home / "profile" / "cv.md").write_text("CV: 6 years data engineering in Python and Spark. " * 10)
    return home


class TestParsers(unittest.TestCase):
    def test_greenhouse(self):
        jobs = SOURCE_TYPES["greenhouse"].parse(load("greenhouse.json"), "Acme")
        self.assertEqual(len(jobs), 4)
        j = jobs[0]
        self.assertEqual(j.key, "greenhouse:1001")
        self.assertIn("5+ years Python", j.description)
        self.assertNotIn("&lt;", j.description)
        self.assertTrue(j.posted_at.startswith("2026-09-27"))

    def test_lever(self):
        jobs = SOURCE_TYPES["lever"].parse(load("lever.json"), "Acme")
        j = jobs[0]
        self.assertEqual(j.title, "Analytics Engineer")
        self.assertEqual(j.workplace, "hybrid")
        self.assertIn("Requirements:", j.description)
        self.assertIn("Remote - Israel", j.location)

    def test_ashby_skips_unlisted(self):
        jobs = SOURCE_TYPES["ashby"].parse(load("ashby.json"), "Acme")
        self.assertEqual([j.native_id for j in jobs], ["ash-1"])
        self.assertEqual(jobs[0].workplace, "hybrid")
        self.assertIn("Remote, Israel", jobs[0].location)

    def test_workable(self):
        j = SOURCE_TYPES["workable"].parse(load("workable.json"), "Acme")[0]
        self.assertEqual(j.native_id, "ABC123")
        self.assertIn("Tel Aviv", j.location)
        self.assertIn("SQL", j.description)

    def test_comeet(self):
        j = SOURCE_TYPES["comeet"].parse(load("comeet.json"), "Acme")[0]
        self.assertEqual(j.title, "Data Platform Engineer")
        self.assertIn("Requirements:", j.description)
        self.assertEqual(j.workplace, "hybrid")

    def _eml(self, name):
        return email.message_from_bytes((FIX / name).read_bytes(), policy=policy.default)

    def test_linkedin_text(self):
        jobs = parse_linkedin(self._eml("linkedin_alert.eml"))
        self.assertEqual([j.native_id for j in jobs], ["4012345678", "4087654321"])
        self.assertEqual(jobs[0].title, "Senior Data Engineer")
        self.assertEqual(jobs[0].company, "Acme")
        self.assertIn("Tel Aviv", jobs[0].location)
        self.assertEqual(jobs[1].company, "Globex Ltd")
        self.assertEqual(jobs[0].url, "https://www.linkedin.com/jobs/view/4012345678/")

    def test_linkedin_html_fallback(self):
        jobs = parse_linkedin(self._eml("linkedin_alert_html_only.eml"))
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1].title, "Analytics Engineer")
        self.assertEqual(jobs[1].company, "Globex Ltd")

    def test_detect(self):
        self.assertEqual(match_text("https://boards.greenhouse.io/riskified")["slug"], "riskified")
        self.assertEqual(match_text('<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme">')["slug"], "acme")
        self.assertEqual(match_text("https://jobs.eu.lever.co/acme")["region"], "eu")
        self.assertEqual(match_text("https://jobs.ashbyhq.com/lemonade")["ats"], "ashby")
        self.assertEqual(match_text("https://apply.workable.com/acme/")["slug"], "acme")
        c = match_text("https://www.comeet.com/jobs/acme/A1.234")
        self.assertEqual((c["ats"], c["uid"]), ("comeet", "A1.234"))
        w = match_text("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Israel/X_JR1")
        self.assertEqual((w["ats"], w["slug"], w["host"], w["site"]),
                         ("workday", "nvidia", "nvidia.wd5.myworkdayjobs.com", "NVIDIAExternalCareerSite"))
        self.assertEqual(match_text("https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs")["site"],
                         "External")

    def test_eightfold(self):
        from jobradar.sources.eightfold import parse_position
        search = load("eightfold_search.json")["data"]["positions"]
        details = load("eightfold_position.json")["data"]
        j = parse_position({**search[0], **details}, "Microsoft", "apply.careers.microsoft.com")
        self.assertEqual(j.key, "eightfold:1001")
        self.assertEqual(j.url, "https://apply.careers.microsoft.com/careers/job/1001")
        self.assertEqual((j.workplace, j.department), ("onsite", "Data Science"))
        self.assertIn("3+ years of experience with Python", j.description)
        self.assertIn("& ship", j.description)
        self.assertTrue(j.posted_at.startswith("2026-10-07"))
        listed = parse_position(search[1], "Microsoft", "apply.careers.microsoft.com")
        self.assertEqual((listed.description, listed.workplace), ("", "hybrid"))

    def test_google_careers(self):
        from jobradar.sources.google_careers import parse_page
        jobs, total = parse_page((FIX / "google_careers.html").read_text(encoding="utf-8"))
        self.assertEqual((len(jobs), total), (2, 2))
        g, w = jobs
        self.assertEqual(g.key, "google:111")
        self.assertEqual(g.location, "Tel Aviv, Israel; Haifa, Israel")
        self.assertIn("Join the Ads team & help.", g.description)
        self.assertIn("2 years of experience with Python", g.description)
        self.assertTrue(g.posted_at.startswith("2026-10-07"))
        self.assertEqual(w.company, "Waze")                  # Waze jobs are listed on Google Careers
        with self.assertRaises(RuntimeError):
            parse_page("<html>captcha</html>")

    def test_workday(self):
        from jobradar.sources import workday as wd
        listing = load("workday_jobs.json")
        self.assertEqual(wd.country_facet(listing["facets"], "Israel"), {"locationHierarchy1": ["il1"]})
        sites_only = [f for f in listing["facets"][1]["values"] if f["facetParameter"] == "locations"]
        self.assertEqual(wd.country_facet(sites_only, "Israel"), {"locations": ["site-ta", "site-yk"]})
        self.assertEqual(wd.country_facet(listing["facets"], "Germany"), {})
        self.assertEqual([wd.posted_days_ago(p["postedOn"]) for p in listing["jobPostings"]], [0, 3, 30])
        self.assertEqual(wd.posted_days_ago("Posted Yesterday"), 1)
        item = listing["jobPostings"][0]
        j = wd.parse_detail(load("workday_job.json"), item, "Acme", "acme.wd5.myworkdayjobs.com", "AcmeCareers")
        self.assertEqual(j.key, "workday:Data-Scientist_JR1001")
        self.assertEqual(j.location, "Israel, Tel Aviv; Israel, Yokneam")
        self.assertIn("3+ years of experience in Python", j.description)
        self.assertIn("& with", j.description)
        self.assertTrue(j.posted_at.startswith("2026-10-07"))
        old = wd.parse_list_item(listing["jobPostings"][2], "Acme", "acme.wd5.myworkdayjobs.com", "AcmeCareers")
        self.assertEqual((old.description, old.url),
                         ("", "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/Israel-Haifa/Product-Manager_JR1003"))
        multi = wd.parse_list_item(listing["jobPostings"][1], "Acme", "h", "s", "Israel")
        self.assertEqual(multi.location, "Israel")   # "2 Locations" says nothing; the search was by country


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.home = make_home()
        self.cfg = Config(self.home)
        self.store = Store(self.cfg.path("paths.db"))
        for name, ats in (("greenhouse.json", "greenhouse"), ("lever.json", "lever"),
                          ("ashby.json", "ashby"), ("comeet.json", "comeet")):
            for p in SOURCE_TYPES[ats].parse(load(name), "Acme"):
                self.store.upsert_job(p)
        msg = email.message_from_bytes((FIX / "linkedin_alert.eml").read_bytes(), policy=policy.default)
        for p in parse_linkedin(msg):
            self.store.upsert_job(p)
        self.store.commit()
        self.store.close()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_full_run_with_fake_backend(self):
        from jobradar.pipeline import run_pipeline
        stats = run_pipeline(self.cfg, ["dedupe", "hard_filter", "triage", "deep_eval", "notify", "report"])
        # LinkedIn "Senior Data Engineer @ Acme" duplicates the Greenhouse posting
        self.assertGreaterEqual(stats["dedupe"]["duplicates"], 1)
        # intern + New York PM are filtered out
        self.assertGreaterEqual(stats["hard_filter"]["filtered_out"], 2)
        self.assertGreater(stats["triage"]["calls"], 0)
        self.assertGreater(stats["deep_eval"]["evaluated"], 0)
        self.assertGreaterEqual(stats["notify"]["pings"], 1)
        self.assertTrue(stats["report"]["written"])
        store = Store(self.cfg.path("paths.db"))
        counts = store.counts_by_status()
        self.assertEqual(counts.get(Status.LIGHT_MATCH.value), 1)  # Globex: email-only, no description
        dup = store.db.execute("SELECT * FROM jobs WHERE status='duplicate'").fetchone()
        self.assertEqual(dup["source"], "linkedin_email")
        # pings are not repeated on the next run
        from jobradar.pipeline import run_pipeline as rp
        again = rp(self.cfg, ["notify"])
        self.assertEqual(again["notify"]["pings"], 0)
        store.close()

    def test_budget_limits_leave_work_pending(self):
        from jobradar.pipeline import run_pipeline
        stats = run_pipeline(self.cfg, ["dedupe", "hard_filter", "triage", "deep_eval"],
                             overrides={"max_deep": 1})
        self.assertEqual(stats["deep_eval"]["evaluated"], 1)
        self.assertGreaterEqual(stats["deep_eval"]["left_pending"], 1)

    def test_labels_become_examples(self):
        from jobradar.examples import deep_examples, triage_examples
        store = Store(self.cfg.path("paths.db"))
        store.set_label(1, "good", "exactly my stack", "test")
        store.set_label(2, "bad", "sales role", "test")
        self.assertIn("exactly my stack", triage_examples(store, 10))
        self.assertIn('label="bad"', deep_examples(store, self.cfg))
        self.assertNotIn("exactly my stack", deep_examples(store, self.cfg, exclude_job_id=1))
        store.close()


class TestSetupStatus(unittest.TestCase):
    def test_reports_next_step(self):
        from jobradar.setup_status import collect
        home = make_home()
        try:
            st = collect(Config(home))
            ids = [s["id"] for s in st["steps"]]
            self.assertIn("calibrate", ids)
            self.assertFalse(st["complete"])
            self.assertIsNotNone(st["next_step"])
            by = {s["id"]: s for s in st["steps"]}
            self.assertFalse(by["companies"]["done"])          # companies.yaml is empty
            self.assertIsNotNone(by["companies"]["next"])
        finally:
            shutil.rmtree(home, ignore_errors=True)


class TestTracker(unittest.TestCase):
    def setUp(self):
        from jobradar.models import JobPosting
        from jobradar.tracker import Tracker
        self.home = make_home()
        self.store = Store(Config(self.home).path("paths.db"))
        ids = []
        for n, (status, reason) in enumerate([(Status.LIGHT_MATCH, "triage yes"), (Status.EVALUATED, "stretch"),
                                              (Status.EVALUATED, "no"), (Status.FILTERED_OUT, "title")]):
            jid, _ = self.store.upsert_job(JobPosting("greenhouse", str(n), "Acme", f"Analyst {n}", f"https://x/{n}"))
            self.store.set_status(jid, status, reason)
            ids.append(jid)
        self.store.commit()
        self.ids = ids
        self.t = Tracker(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def test_sync_takes_only_what_was_surfaced(self):
        self.assertEqual(self.t.sync(), 2)  # light match + evaluated (not "no", not filtered)
        self.assertEqual(self.t.sync(), 0)
        f = self.t.funnel(7)
        self.assertEqual((f["surfaced"], f["reviewed"], f["applied"]), (2, 0, 0))

    def test_states_funnel_application_and_labels(self):
        light, deep = self.ids[0], self.ids[1]
        self.t.set_state(light, "dismissed", "not my field")
        self.t.set_state(deep, "applied")
        f = self.t.funnel(7)
        self.assertEqual((f["surfaced"], f["reviewed"], f["applied"], f["reviewed_pct"], f["applied_pct"]),
                         (2, 2, 1, 100, 50))
        app = self.store.db.execute("SELECT * FROM applications WHERE job_id = ?", (deep,)).fetchone()
        self.assertEqual(app["status"], "applied")
        lab = self.store.db.execute("SELECT * FROM labels WHERE job_id = ?", (light,)).fetchone()
        self.assertEqual((lab["label"], lab["note"], lab["origin"]), ("bad", "not my field", "tracker"))
        # undo: back to review -> process withdrawn, implicit label removed
        self.t.set_state(deep, "to_review")
        app = self.store.db.execute("SELECT * FROM applications WHERE job_id = ?", (deep,)).fetchone()
        self.assertEqual(app["status"], "withdrawn")
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM labels WHERE job_id = ?", (deep,)).fetchone())
        self.assertEqual(self.t.funnel(7)["reviewed"], 1)

    def test_own_rating_wins_and_seeds_state(self):
        light, deep = self.ids[0], self.ids[1]
        self.store.set_label(deep, "good", "love it", "review")
        self.t.sync()
        row = self.store.db.execute("SELECT state FROM job_tracking WHERE job_id = ?", (deep,)).fetchone()
        self.assertEqual(row["state"], "to_apply")
        self.t.set_state(deep, "dismissed", "changed my mind")
        lab = self.store.db.execute("SELECT * FROM labels WHERE job_id = ?", (deep,)).fetchone()
        self.assertEqual((lab["label"], lab["origin"]), ("good", "review"))  # never overwritten

    def test_items_and_digest(self):
        self.t.sync()
        items = self.t.items(7)
        self.assertEqual({i["kind"] for i in items}, {"light"})  # no deep evaluation stored in this fixture
        title, body = self.t.digest_text()
        self.assertIn("2 לעיון", title)
        self.assertIn("שבוע: 2 הגיעו", body)


class TestNetwork(unittest.TestCase):
    def setUp(self):
        from jobradar.network import Network
        self.home = make_home()
        self.cfg = Config(self.home)
        self.store = Store(self.cfg.path("paths.db"))
        self.net = Network(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def test_linkedin_import_and_company_match(self):
        res = self.net.import_linkedin_csv(FIX / "linkedin_connections.csv")
        self.assertEqual(res["created"], 3)
        again = self.net.import_linkedin_csv(FIX / "linkedin_connections.csv")
        self.assertEqual((again["created"], again["updated"]), (0, 3))
        self.assertEqual([c["name"] for c in self.net.contacts_at("WSC Sports Technologies")], ["Dana Levi"])
        self.assertEqual([c["name"] for c in self.net.contacts_at("Acme")], ["Avi Cohen"])
        self.assertEqual(self.net.contacts_at("Acmeville"), [])

    def test_manual_details_survive_reimport(self):
        self.net.import_linkedin_csv(FIX / "linkedin_connections.csv")
        dana = self.net.resolve_contact("Dana")
        self.net.update_contact(dana["id"], strength=3, relationship="friend", notes="army friend")
        self.net.import_linkedin_csv(FIX / "linkedin_connections.csv")
        dana = self.net.get_contact(dana["id"])
        self.assertEqual((dana["strength"], dana["relationship"], dana["notes"]), (3, "friend", "army friend"))

    def test_log_followups_and_applications(self):
        cid, _ = self.net.add_contact("Dana Levi", company="Wix", relationship="friend", strength=3)
        self.net.log(cid, "she will pass my CV to the data team", "whatsapp", follow_up_on="+3")
        self.net.log(cid, "old chat", "call", on="2026-01-01")
        due = self.net.followups(7)
        self.assertEqual(len(due), 1)
        app = self.net.add_application("Wix", "Data Engineer", status="applied", referral_contact_id=cid)
        self.net.update_application(app, status="interview", note="first round Sunday", next_step="prep SQL",
                                    next_step_on="+2")
        a = self.net.get_application(app)
        self.assertEqual(a["status"], "interview")
        self.assertIsNotNone(a["applied_on"])
        self.assertEqual([e["status"] for e in self.net.app_events(app)], ["applied", "interview"])
        self.net.mark_followup_done(due[0]["id"])
        self.assertEqual(self.net.followups(7), [])
        with self.assertRaises(ValueError):
            self.net.add_application("Wix", "X", status="dreaming")

    def test_resolve_ambiguous(self):
        self.net.add_contact("Dana Levi", company="Wix")
        self.net.add_contact("Dana Cohen", company="Monday")
        with self.assertRaises(ValueError):
            self.net.resolve_contact("Dana")
        self.assertEqual(self.net.resolve_contact("Dana Cohen")["company"], "Monday")


class TestTechmapAndFavorites(unittest.TestCase):
    def test_techmap_parse(self):
        from jobradar.sources.techmap import TechmapSource
        jobs = TechmapSource.parse((FIX / "techmap_software.csv").read_text(encoding="utf-8"), "software")
        self.assertGreater(len(jobs), 5)
        j = jobs[0]
        self.assertTrue(j.location.endswith("Israel"))
        self.assertNotIn("utm_", j.url)
        self.assertTrue(j.native_id)
        self.assertEqual(j.source, "techmap")

    def test_favorite_fast_track_and_ping_context(self):
        from jobradar.network import Network
        from jobradar.pipeline import run_pipeline
        home = make_home()
        try:
            (home / "config" / "companies.yaml").write_text(
                "companies:\n  - name: Globex\n    favorite: true\n", encoding="utf-8")
            cfg = Config(home)
            store = Store(cfg.path("paths.db"))
            for p in SOURCE_TYPES["lever"].parse(load("lever.json"), "Globex"):
                p.posted_at = None  # fixture date is old
                store.upsert_job(p)
            Network(store).add_contact("Noa Bar", company="Globex", relationship="ex-colleague", strength=2)
            store.commit()
            store.close()
            stats = run_pipeline(cfg, ["hard_filter", "triage", "deep_eval", "notify"])
            self.assertEqual(stats["triage"].get("favorites_fast_tracked"), 1)
            self.assertEqual(stats["triage"]["calls"], 0)
            store = Store(cfg.path("paths.db"))
            ev = store.latest_evaluation(1, "deep")
            self.assertTrue(ev["decision"]["favorite"])
            from jobradar.stages.notify import format_match, network_context
            title, body = format_match(store.get_job(1), ev, network_context(Network(store), "Globex"), True)
            self.assertTrue(title.startswith("⭐"))
            self.assertIn("Noa Bar", body)
            store.close()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_favorite_thresholds(self):
        from jobradar.stages.deep_eval import decide
        cfg = Config(make_home())
        ev = {"scores": {"capability": 6, "desire": 5, "screen_pass": 4}, "verdict": "stretch"}
        self.assertFalse(decide(ev, cfg)["notify"])
        self.assertTrue(decide(ev, cfg, favorite=True)["notify"])


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.home = make_home()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_cli(self, *argv):
        import contextlib
        import io as _io
        from jobradar.cli import main
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--home", str(self.home), *argv])
        return code, buf.getvalue()

    def test_network_commands_and_view(self):
        self.assertEqual(self.run_cli("net", "import-linkedin", str(FIX / "linkedin_connections.csv"))[0], 0)
        self.assertEqual(self.run_cli("net", "update", "Dana", "--strength", "3", "--relationship", "friend")[0], 0)
        code, out = self.run_cli("net", "log", "Dana", "--summary", "coffee, she offered a referral",
                                 "--channel", "meeting", "--follow-up", "+2")
        self.assertEqual(code, 0)
        code, out = self.run_cli("net", "at", "WSC Sports Technologies", "--json")
        self.assertEqual(json.loads(out)[0]["name"], "Dana Levi")
        code, out = self.run_cli("apps", "add", "--company", "WSC Sports", "--title", "Data Engineer",
                                 "--status", "applied", "--via", "Dana")
        self.assertEqual(code, 0)
        code, out = self.run_cli("apps", "list", "--json")
        self.assertEqual(json.loads(out)[0]["referral_name"], "Dana Levi")
        code, out = self.run_cli("net", "followups", "--json")
        self.assertEqual(len(json.loads(out)), 1)
        self.assertEqual(self.run_cli("view")[0], 0)
        page = (self.home / "reports" / "network.html").read_text(encoding="utf-8")
        self.assertIn("Dana Levi", page)
        self.assertIn("Data Engineer", page)

    def test_import_techmap_and_filter_test(self):
        shutil.copytree(ROOT / "seeds", self.home / "seeds")
        code, out = self.run_cli("import-techmap", "--sizes", "xl")
        self.assertEqual(code, 0)
        cfg = Config(self.home)
        self.assertTrue(cfg.companies and all(c.get("ats") for c in cfg.companies))
        code, out = self.run_cli("import-techmap", "--sizes", "xl")  # idempotent
        self.assertIn("0", out.splitlines()[0])
        store = Store(cfg.path("paths.db"))
        for p in SOURCE_TYPES["greenhouse"].parse(load("greenhouse.json"), "Acme"):
            p.posted_at = None
            store.upsert_job(p)
        store.commit()
        store.close()
        code, out = self.run_cli("filter-test", "--json")
        res = json.loads(out)
        self.assertEqual(res["total"], 4)
        self.assertTrue(any("intern" in k for k in res["excluded_by_rule"]))
        code, out = self.run_cli("filter-test", "--apply")
        self.assertIn("now_pending", out)


class TestExampleFilters(unittest.TestCase):
    """The shipped filters.example.yaml encodes decisions the user made; keep them true."""

    def setUp(self):
        import yaml
        from jobradar.stages.hard_filter import HardFilter
        f = yaml.safe_load((ROOT / "config" / "filters.example.yaml").read_text(encoding="utf-8"))
        f["max_age_days"] = None
        self.hf = HardFilter(f)

    def ok(self, title, desc=""):
        return self.hf.check({"company": "A", "title": title, "location": "Tel Aviv, Israel", "workplace": "",
                              "department": "", "description": desc, "posted_at": None})[0]

    def test_excluded_titles(self):
        for t in ["Senior Data Engineer", "Sr. Backend Developer", "Principal Engineer", "Staff ML Engineer",
                  "Team Lead, Payments", "Tech Lead", "Solutions Architect", "Head of Data", "ASIC Design Engineer",
                  "RTL Design Engineer", "Physical Design Engineer", "Hardware Engineer", "Embedded Software Engineer",
                  "Firmware Engineer", "FPGA Engineer", "Hardware Validation Engineer", "SoC Verification Engineer",
                  "מהנדס/ת אלקטרוניקה", "ראש צוות פיתוח"]:
            self.assertFalse(self.ok(t), t)

    def test_kept_titles(self):
        for t in ["Data Engineer", "Backend Developer", "Lead Generation Specialist", "Software Engineer",
                  "Product Manager", "Engineering Manager", "QA Automation Engineer", "SOC Analyst",
                  "Data Analyst", "Dashboard Developer"]:
            self.assertTrue(self.ok(t), t)

    def test_experience(self):
        self.assertFalse(self.ok("Data Engineer", "Requirements:\n- 5+ years of experience with Python"))
        self.assertTrue(self.ok("Data Engineer", "Requirements:\n- 3+ years of experience with Python\n"
                                                 "- 6+ years experience in Go - an advantage"))
        self.assertTrue(self.ok("Data Engineer", "Founded 15 years ago.\n- Experience with SQL"))


class TestHardFilter(unittest.TestCase):
    def test_rules(self):
        from jobradar.stages.hard_filter import HardFilter
        hf = HardFilter({"locations": {"allow_any_of": ["israel", "tel aviv", "remote"], "allow_unknown": False},
                         "title": {"include_any": [], "exclude_any": [r"\bintern\b"]},
                         "max_age_days": 30})
        base = {"company": "A", "title": "Data Engineer", "location": "Tel Aviv", "workplace": "",
                "department": "", "description": "", "posted_at": None}
        self.assertTrue(hf.check(base)[0])
        self.assertFalse(hf.check({**base, "title": "Data Intern"})[0])
        self.assertFalse(hf.check({**base, "location": "Berlin"})[0])
        self.assertTrue(hf.check({**base, "location": "Berlin", "workplace": "remote"})[0])
        self.assertFalse(hf.check({**base, "location": ""})[0])
        self.assertFalse(hf.check({**base, "posted_at": "2020-01-01T00:00:00+00:00"})[0])

    def test_location_deny(self):
        from jobradar.stages.hard_filter import HardFilter
        hf = HardFilter({"locations": {"allow_any_of": ["israel"], "deny_any_of": ["jerusalem", "ירושלים"]}})
        base = {"company": "A", "title": "Data Engineer", "location": "Tel Aviv, Israel", "workplace": "",
                "department": "", "description": "", "posted_at": None}
        self.assertTrue(hf.check(base)[0])
        self.assertFalse(hf.check({**base, "location": "Jerusalem, Israel"})[0])
        self.assertFalse(hf.check({**base, "location": "ירושלים"})[0])
        fav = HardFilter({"locations": {"deny_any_of": ["jerusalem"]}}, lambda c: c == "Mobileye")
        self.assertTrue(fav.check({**base, "company": "Mobileye", "location": "Jerusalem"})[0])
        self.assertFalse(fav.check({**base, "company": "Other", "location": "Jerusalem"})[0])


class TestEnrich(unittest.TestCase):
    def test_match_url(self):
        from jobradar.stages.enrich import match_url
        self.assertEqual(match_url("https://www.comeet.com/jobs/acme/30.005/data-analyst/87.406?utm_x=1"),
                         ("comeet", ("acme", "30.005", "87.406")))
        self.assertEqual(match_url("https://jobs.lever.co/acme/7f3812ca-d4c6-4d7f-acb0-9206c2984a64")[0], "lever")
        self.assertEqual(match_url("https://boards.greenhouse.io/acme/jobs/4455667"),
                         ("greenhouse", ("acme", "4455667")))
        self.assertIsNone(match_url("https://il.linkedin.com/jobs/view/data-scientist-at-x-4465725305"))

    def test_parsers(self):
        from jobradar.stages.enrich import comeet_token, parse_description
        page = (FIX / "comeet_position_page.html").read_text(encoding="utf-8")
        self.assertEqual(comeet_token(page), "ABCDEF0123456789ABCDEF")
        d = parse_description("comeet", load("comeet_position.json"))
        self.assertIn("checkout funnel", d)
        self.assertIn("A/B testing", d)
        self.assertIn("planning processes", parse_description("lever", load("lever_posting.json")))
        g = parse_description("greenhouse", load("greenhouse_job.json"))
        self.assertIn("forecasting models", g)
        self.assertNotIn("&lt;", g)

    def test_stage_moves_light_match_to_deep(self):
        from jobradar import http
        from jobradar.config import Config
        from jobradar.models import JobPosting
        from jobradar.pipeline import Context
        from jobradar.stages.enrich import EnrichStage
        cfg = Config(make_home())
        store = Store(cfg.path("paths.db"))
        url = "https://www.comeet.com/jobs/acme/30.005/data-analyst/87.406"
        jid, _ = store.upsert_job(JobPosting(source="techmap", native_id="x1", company="Acme",
                                             title="Data Analyst", url=url))
        lid, _ = store.upsert_job(JobPosting(source="techmap", native_id="x2", company="Acme",
                                             title="Data Scientist", url="https://il.linkedin.com/jobs/view/123456789"))
        store.set_status(jid, Status.LIGHT_MATCH)
        store.set_status(lid, Status.LIGHT_MATCH)
        store.commit()
        page = (FIX / "comeet_position_page.html").read_text(encoding="utf-8")
        orig = http.get_text, http.get_json
        http.get_text = lambda u, headers=None: page
        http.get_json = lambda u, headers=None: load("comeet_position.json")
        try:
            stats = EnrichStage().run(Context(config=cfg, store=store))
            again = EnrichStage().run(Context(config=cfg, store=store))
        finally:
            http.get_text, http.get_json = orig
        self.assertEqual(stats["to_deep"], 1)
        self.assertEqual(store.get_job(jid)["status"], Status.DEEP_PENDING.value)
        self.assertIn("checkout funnel", store.get_job(jid)["description"])
        self.assertEqual(store.get_job(lid)["status"], Status.LIGHT_MATCH.value)  # LinkedIn is never fetched
        self.assertEqual(again["tried"], 0)


class TestScoringMethod(unittest.TestCase):
    """deep-v4 arithmetic, checked against the worked examples in
    archive/briefs/2026-10-01-method-design/outputs/*.md"""

    @staticmethod
    def cap(musts, gate="pass", adjustments=()):
        from jobradar.scoring import capability_from_analysis
        return capability_from_analysis({
            "job_analysis": {"gate": {"result": gate},
                             "must_haves": [{"weight": w, "gap": g} for w, g in musts]},
            "capability_calc": {"adjustments": list(adjustments)}})

    def test_capability_examples(self):
        self.assertEqual(self.cap([("primary", "none"), ("core", "ramp"), ("core", "none")]), 7)        # A
        self.assertEqual(self.cap([("primary", "none"), ("core", "ramp"), ("core", "months")],
                                  gate="scope_stretch"), 5)                                             # B
        self.assertEqual(self.cap([("primary", "none")] * 3 + [("supporting", "ramp")]), 8)            # C
        self.assertEqual(self.cap([("primary", "ramp"), ("core", "ramp"), ("core", "ramp")]), 6)       # D
        self.assertEqual(self.cap([("primary", "none")] * 3, adjustments=["proven_in_role"]), 9)       # E
        self.assertEqual(self.cap([("primary", "absent")], gate="stop_scope"), 3)                      # F
        self.assertEqual(self.cap([("primary", "absent"), ("core", "months")]), 2)                     # #941
        from jobradar.scoring import capability_from_analysis
        self.assertIsNone(capability_from_analysis({}))                                                # deep-v3 result

    def test_screen_examples(self):
        from jobradar.scoring import screen_from_check

        def scr(family, level, signals, hits, caps=()):
            return screen_from_check({"screen_check": {
                "family": family, "level": level, "keyword_hits": hits, "adjustments": [],
                "signals": dict(zip(("title", "keywords", "years", "education", "domain", "results"), signals)),
                "caps": [{"code": c} for c in caps]}})
        self.assertEqual(scr("engineering", "senior", (2, 2, 1, 2, 0, 2), ["bullet", "bullet", "none", "bullet"]), 8)
        self.assertEqual(scr("analytics", "mid", (1, 2, 0, 2, 0, 1), ["bullet"] * 3, ["YEARS4"]), 4)
        self.assertEqual(scr("analytics", "entry", (1, 1, 0, 2, 1, 2),
                             ["bullet", "none", "skills", "bullet"], ["OVERQUALIFIED"]), 4)
        self.assertEqual(scr("research", "entry", (2, 1, 0, 2, 1, 2), ["none", "bullet", "bullet"]), 6)

    def test_verdict_rule(self):
        from jobradar.scoring import verdict_for

        def v(cap, des, scr, gate="pass", caps=()):
            return verdict_for({"job_analysis": {"gate": {"result": gate}},
                                "screen_check": {"caps": [{"code": c} for c in caps]},
                                "scores": {"capability": cap, "desire": des, "screen_pass": scr}})
        self.assertEqual(v(8, 7, 6), "strong")
        self.assertEqual(v(8, 7, 2), "good")      # low screen never downgrades good
        self.assertEqual(v(6, 7, 3), "stretch")
        self.assertEqual(v(6, 7, 2), "no")
        self.assertEqual(v(9, 3, 9), "no")        # desire dealbreaker
        self.assertEqual(v(7, 7, 7, caps=["KO1"]), "no")
        self.assertEqual(v(3, 8, 5, gate="stop_scope"), "no")

    def test_pipeline_applies_verdict_rule(self):
        from jobradar.llm.fake import fake_deep_result
        from jobradar.stages.deep_eval import apply_method_checks
        r = fake_deep_result("x", 8, desire=7, screen=2)
        r["verdict"] = "strong"                    # model disagrees with the rule
        apply_method_checks(r)
        self.assertEqual(r["verdict"], "good")
        self.assertEqual(r["checks"]["verdict_model"], "strong")


class TestDedupeLocation(unittest.TestCase):
    def test_hebrew_city_is_compatible(self):
        from jobradar.stages.dedupe import _loc_compatible
        self.assertTrue(_loc_compatible("נתניה, Israel", "Netanya"))
        self.assertTrue(_loc_compatible("Tel Aviv, Israel", "Tel Aviv"))
        self.assertFalse(_loc_compatible("Berlin, Germany", "Tel Aviv"))
        self.assertTrue(_loc_compatible("Tel-Aviv, Israel", "Tel Aviv-Yafo"))  # comeet vs LinkedIn spelling

    def test_linkedin_badge_line_is_not_a_field(self):
        lines = ["Your job alert for Data Analyst", "", "Product Analyst", "Faye", "Tel Aviv-Yafo", "Fast growing",
                 "View job: https://www.linkedin.com/comm/jobs/view/4475383668/?x=1", ""]
        header = ["From: a@linkedin.com", "Subject: t", "Content-Type: text/plain", ""]
        raw = chr(10).join(header + lines)
        j = parse_linkedin(email.message_from_string(raw, policy=policy.default))[0]
        self.assertEqual((j.title, j.company, j.location), ("Product Analyst", "Faye", "Tel Aviv-Yafo"))


class TestDedupeWindow(unittest.TestCase):
    def test_same_appearance(self):
        from jobradar.stages.dedupe import same_appearance
        now = datetime.now(timezone.utc)
        ago = lambda d: (now - timedelta(days=d)).isoformat()  # noqa: E731
        board = {"posted_at": ago(4), "first_seen_at": ago(4)}           # career board, seen when posted
        late_listing = {"posted_at": ago(4), "first_seen_at": ago(0)}    # techmap lists it 4 days later
        bumped = {"posted_at": ago(0), "first_seen_at": ago(0)}          # same title re-posted today
        self.assertTrue(same_appearance(late_listing, board, 2))
        self.assertFalse(same_appearance(bumped, board, 2))
        self.assertTrue(same_appearance(bumped, board, None))
        self.assertTrue(same_appearance(bumped, {"posted_at": None, "first_seen_at": None}, 2))


class TestLateSources(unittest.TestCase):
    def test_age_counts_from_first_seen_except_backfill(self):
        from jobradar.stages.hard_filter import HardFilter, job_age_days
        now = datetime.now(timezone.utc)
        today, week_ago = now.isoformat(), (now - timedelta(days=7)).isoformat()
        base = {"title": "Data Analyst", "company": "Acme", "location": "Tel Aviv", "workplace": "",
                "department": "", "description": ""}
        listed_late = {**base, "source": "techmap", "posted_at": week_ago, "first_seen_at": today}
        board = {**base, "source": "greenhouse", "posted_at": week_ago, "first_seen_at": today}
        self.assertLess(job_age_days(listed_late, {"techmap"}), 1)
        self.assertGreater(job_age_days(board, {"techmap"}), 6)
        hf = HardFilter({"late_sources": ["techmap"], "max_age_days": 2})
        self.assertTrue(hf.check(listed_late)[0])
        self.assertFalse(hf.check(board)[0])
        first_fetch = HardFilter({"late_sources": ["techmap"], "max_age_days": 2}, backfill_days={"techmap": today[:10]})
        self.assertFalse(first_fetch.check(listed_late)[0])   # the source's first fetch is not a flood


class TestLightDigestVerdicts(unittest.TestCase):
    def test_yaml_booleans(self):
        from jobradar.stages.notify import light_digest_allowed
        self.assertEqual(light_digest_allowed([True]), {"yes"})          # YAML `[yes]`
        self.assertEqual(light_digest_allowed([True, "maybe"]), {"yes", "maybe"})
        self.assertEqual(light_digest_allowed(None), {"yes"})


class TestNotifyFreshness(unittest.TestCase):
    def test_too_old(self):
        from jobradar.stages.notify import too_old
        now = datetime.now(timezone.utc)
        fresh = {"posted_at": (now - timedelta(hours=10)).isoformat(), "first_seen_at": now.isoformat()}
        stale = {"posted_at": (now - timedelta(hours=60)).isoformat(), "first_seen_at": now.isoformat()}
        no_date = {"posted_at": None, "first_seen_at": (now - timedelta(hours=72)).isoformat()}
        self.assertFalse(too_old(fresh, 48))
        self.assertTrue(too_old(stale, 48))
        self.assertTrue(too_old(no_date, 48))
        self.assertFalse(too_old(stale, None))
        self.assertFalse(too_old({"source": "techmap", **stale}, 48, {"techmap"}))


class TestClaudeCLIBackend(unittest.TestCase):
    def setUp(self):
        self.home = make_home()
        self.bin = self.home / "claude"
        self.bin.write_text(f"#!{sys.executable}\n" + (ROOT / "tests" / "fake_claude.py").read_text())
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        (self.home / "config" / "config.yaml").write_text(
            f"llm:\n  backend: claude_cli\n  claude_bin: {self.bin}\n  sandbox_dir: {self.home / 'sandbox'}\n",
            encoding="utf-8")
        self.cfg = Config(self.home)
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-be-stripped"

    def tearDown(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("FAKE_CLAUDE_MODE", None)
        shutil.rmtree(self.home, ignore_errors=True)

    def _call(self, schema):
        from jobradar.llm.claude_cli import ClaudeCLIBackend
        return ClaudeCLIBackend(self.cfg).complete_json(
            system="sys", user='<job id="7">x</job>', schema=schema, model="haiku", purpose="t")

    def test_structured_output(self):
        from jobradar.llm.schemas import TRIAGE_SCHEMA
        r = self._call(TRIAGE_SCHEMA)
        self.assertEqual(r.data["results"][0]["job_id"], "7")
        self.assertTrue(r.meta["structured"])

    def test_text_fallback(self):
        from jobradar.llm.schemas import DEEP_SCHEMA
        os.environ["FAKE_CLAUDE_MODE"] = "nostructured"
        r = self._call(DEEP_SCHEMA)
        self.assertEqual(r.data["scores"]["capability"], 8)

    def test_usage_limit(self):
        from jobradar.llm import UsageLimitReached
        from jobradar.llm.schemas import PROBE_SCHEMA
        os.environ["FAKE_CLAUDE_MODE"] = "limit"
        with self.assertRaises(UsageLimitReached):
            self._call(PROBE_SCHEMA)

    def test_error(self):
        from jobradar.llm import LLMError
        from jobradar.llm.schemas import PROBE_SCHEMA
        os.environ["FAKE_CLAUDE_MODE"] = "error"
        with self.assertRaises(LLMError):
            self._call(PROBE_SCHEMA)

    def test_sandbox_left_clean(self):
        from jobradar.llm.schemas import PROBE_SCHEMA
        self._call(PROBE_SCHEMA)
        self.assertEqual(list((self.home / "sandbox").iterdir()), [])

    def test_isolation_flags(self):
        from jobradar.llm.claude_cli import ClaudeCLIBackend
        cmd = ClaudeCLIBackend(self.cfg).build_command("sys.md", {"type": "object"}, "haiku")
        i = cmd.index("--setting-sources")
        self.assertEqual(cmd[i + 1], "")  # no settings, no CLAUDE.md
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertFalse(str(ClaudeCLIBackend(Config(make_home())).sandbox).startswith(str(self.home)))

    def test_tools_only_when_asked(self):
        from jobradar.llm.claude_cli import ClaudeCLIBackend
        cmd = ClaudeCLIBackend(self.cfg).build_command("sys.md", {"type": "object"}, "sonnet",
                                                       tools=["WebSearch", "WebFetch"],
                                                       deny=["WebFetch(domain:linkedin.com)"], max_turns=12)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1: cmd.index("--allowedTools") + 3], ["WebSearch", "WebFetch"])
        d = cmd.index("--disallowedTools")
        self.assertEqual(cmd[d + 1: d + 3], ["mcp__*", "WebFetch(domain:linkedin.com)"])
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "12")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")


class TestCV(unittest.TestCase):
    POSTING = ("We are looking for a Data Engineer to build Python and Spark pipelines. "
               "Requirements: 2+ years of Python, SQL, Airflow. ") * 5

    def setUp(self):
        from jobradar.models import JobPosting
        self.home = make_home("cv:\n  contact_line: 'Tel Aviv · me@example.com · linkedin.com/in/me'\n")
        self.cfg = Config(self.home)
        store = Store(self.cfg.path("paths.db"))
        self.with_desc, _ = store.upsert_job(JobPosting("greenhouse", "1", "Acme Ltd", "Data Engineer (Hybrid)",
                                                        "https://boards.greenhouse.io/acme/jobs/1",
                                                        description=self.POSTING))
        self.linkedin, _ = store.upsert_job(JobPosting("techmap", "2", "Beta", "Data Analyst",
                                                       "https://www.linkedin.com/jobs/view/123456789"))
        store.commit()
        store.close()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_names_and_links(self):
        from jobradar.cv_builder import is_linkedin, slug, title_case_slug
        self.assertEqual(slug("Data Engineer (Hybrid) – Tel Aviv"), "data-engineer-tel-aviv")
        self.assertEqual(title_case_slug("student-researcher-2027"), "Student-Researcher-2027")
        self.assertEqual(slug("מהנדס נתונים"), "")
        self.assertTrue(is_linkedin("https://il.linkedin.com/jobs/view/1"))
        self.assertTrue(is_linkedin("https://lnkd.in/abc"))
        self.assertFalse(is_linkedin("https://boards.greenhouse.io/x/jobs/1"))

    def test_markdown_render(self):
        from jobradar.cv_render import markdown_to_body
        body = markdown_to_body("# Jane Doe\nTel Aviv · jane@x.com · github.com/jane\n\n**Data Engineer**\n\n"
                                "## Experience\n### Engineer | Acme | 2024–present\n- Built **things**.\n"
                                "## Education\n- **B.Sc.** | TAU | 2020\n  Relevant coursework: Algorithms\n")
        self.assertIn('<a href="mailto:jane@x.com">', body)
        self.assertIn('<a href="https://github.com/jane">', body)
        self.assertIn('<span class="when">2024–present</span>', body)
        self.assertIn("<li>Built <strong>things</strong>.", body)
        self.assertIn("<br>Relevant coursework: Algorithms", body)
        self.assertIn('class="headline"', body)

    def test_build_writes_work_files_apart_from_pdf(self):
        from unittest import mock
        from jobradar.cv_builder import CVBuilder
        b = CVBuilder(self.cfg, "fake")
        with mock.patch("jobradar.cv_builder.markdown_to_pdf", return_value={"pages": 1, "font_pt": 10.5}) as pdf:
            meta = b.build(self.with_desc)
        self.assertTrue(meta["base"].endswith(f"_{self.with_desc}_acme_data-engineer"))
        self.assertEqual(meta["pdf"], "CV Fake Candidate - Data Engineer - Acme Ltd.pdf")
        self.assertEqual(pdf.call_args[0][1], self.home / "profile" / "tailored" / meta["pdf"])
        work = self.home / "profile" / "tailored" / "work"
        for suffix in (".md", ".note.md", ".posting.txt", ".json"):
            self.assertTrue((work / f"{meta['base']}{suffix}").exists(), suffix)
        cv = (work / f"{meta['base']}.md").read_text(encoding="utf-8")
        self.assertIn("me@example.com", cv)           # contact filled locally
        self.assertNotIn("<email>", cv)
        self.assertEqual(meta["posting_source"], "jobradar DB")
        self.assertEqual(b.find(self.with_desc)["base"], meta["base"])
        self.assertEqual(b.cv_index(), {self.with_desc: meta["base"]})

    def test_linkedin_needs_a_paste(self):
        from unittest import mock
        from jobradar.cv_builder import CVBuilder, NeedPosting
        b = CVBuilder(self.cfg, "fake")
        with mock.patch("jobradar.http.get_text") as get:
            with self.assertRaises(NeedPosting):
                b.build(self.linkedin)   # fake search finds nothing
            get.assert_not_called()       # LinkedIn is never fetched
        with mock.patch("jobradar.cv_builder.markdown_to_pdf", return_value={"pages": 1, "font_pt": 10.5}):
            meta = b.build(self.linkedin, posting_text=self.POSTING)
        self.assertEqual(meta["posting_source"], "pasted by the user")
        store = Store(self.cfg.path("paths.db"))
        self.assertEqual(store.get_job(self.linkedin)["description"], self.POSTING.strip())
        store.close()


if __name__ == "__main__":
    unittest.main()
