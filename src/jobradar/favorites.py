"""Favorite companies ⭐ - highlighted, never the only ones watched.

Mark a company in config/companies.yaml with `favorite: true`. An entry can be
just a name (no ATS) when the company's jobs reach you via LinkedIn alerts or
the techmap feed:

    - name: Wix
      favorite: true

Effects (all configurable in config.yaml › favorites):
  - optionally skip the cheap triage stage (favorites.skip_triage; off by default
    in this install, so hardware roles at favorites still meet the profile)
  - lower notify thresholds
  - ⭐ in pings, first in digests and reports
  - the evaluator is told the company is a favorite (it may raise "desire")
"""

from __future__ import annotations

from jobradar.network import companies_match
from jobradar.textutil import norm_company


class Favorites:
    def __init__(self, cfg):
        self.names = [norm_company(c.get("name", "")) for c in cfg.companies if c.get("favorite")]
        self.names = [n for n in self.names if n]

    def __bool__(self) -> bool:
        return bool(self.names)

    def is_favorite(self, company: str) -> bool:
        cn = norm_company(company)
        return any(companies_match(cn, f) for f in self.names)
