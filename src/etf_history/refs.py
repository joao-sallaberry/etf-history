"""Reference data from seeds/: which securities are Brazilian, which company they belong to and how many local
shares each one represents."""

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

SEED_FILES = ("securities.csv", "company_aliases.csv")


@dataclass(frozen=True)
class Security:
    country: str
    company: str
    share_ratio: float


@dataclass(frozen=True)
class Refs:
    # isin -> override: ADRs of Brazilian companies, foreign BDRs and foreign issuers known not to be stored
    securities: dict[str, Security] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)  # old issuer code -> current company code
    sha256: str = ""

    def classify(self, isin: str) -> dict:
        """country, company, kind and share_ratio of a security.

        kind: share | unit | bdr | right (Brazilian ISINs), adr (non-BR ISIN in seeds), foreign (anything else).
        company is the B3 issuer code (ISIN chars 2:6, e.g. PETR), after aliases; share_ratio converts the
        security into local shares (PBR ADR = 2 PETR3).
        """
        override = self.securities.get(isin)
        if isin.startswith("BR") and len(isin) == 12:
            if isin[6] in "DR" or not isin[-1].isdigit():
                kind = "right"  # subscription rights and receipts (BRBBDCD14OR9, BRABCBACNPRR)
            elif isin[6:9] == "BDR":
                kind = "bdr"
            elif isin[6:8] == "CD":
                kind = "unit"
            else:
                kind = "share"
            company = override.company if override else isin[2:6]
            country = override.country if override else "BR"
        elif override:
            kind, company, country = "adr", override.company, override.country
        else:
            return {
                "country": isin[:2] if isin[:2].isalpha() else None,
                "company": None,
                "kind": "foreign",
                "share_ratio": None,
            }
        return {
            "country": country,
            "company": self.aliases.get(company, company),
            "kind": kind,
            "share_ratio": override.share_ratio if override else 1.0,
        }

    def is_brazilian(self, isin: str | None) -> bool:
        """Stored in the database: listed on B3 (BR ISIN) or an ADR of a company with shares on B3. BDRs are
        excluded: their issuers (JBS NV, Carrefour SA, XP) are listed abroad and only have a receipt on B3."""
        if not isin:
            return False
        if isin.startswith("BR"):
            return isin[6:9] != "BDR"
        override = self.securities.get(isin)
        return bool(override and override.country == "BR")


def load(seeds_dir: Path) -> Refs:
    digest = hashlib.sha256()
    securities, aliases = {}, {}
    for name in SEED_FILES:
        path = seeds_dir / name
        if path.exists():
            digest.update(path.read_bytes())
    path = seeds_dir / "securities.csv"
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                securities[r["isin"]] = Security(r["country"], r["company"], float(r["share_ratio"] or 1))
    path = seeds_dir / "company_aliases.csv"
    if path.exists():
        with path.open(encoding="utf-8") as f:
            aliases = {r["code"]: r["company"] for r in csv.DictReader(f)}
    return Refs(securities, aliases, digest.hexdigest())
