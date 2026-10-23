import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    data_dir: Path
    seeds_dir: Path
    funds: tuple[str, ...]
    sec_user_agent: str  # "Name email" contact SEC requires for N-PORT downloads; empty = N-PORT disabled

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


def _dotenv(path: Path) -> dict[str, str]:
    """KEY=value lines of the repository's .env (git-ignored); comments and blanks skipped."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def load() -> Settings:
    # Real environment variables win over .env.
    env = _dotenv(REPO_ROOT / ".env") | dict(os.environ)
    data_dir = Path(env.get("ETF_HISTORY_DATA_DIR", REPO_ROOT / "data")).expanduser()
    return Settings(
        db_path=Path(env.get("ETF_HISTORY_DB", data_dir / "etf.db")).expanduser(),
        data_dir=data_dir,
        seeds_dir=Path(env.get("ETF_HISTORY_SEEDS_DIR", REPO_ROOT / "seeds")).expanduser(),
        funds=tuple(f.strip().upper() for f in env.get("ETF_HISTORY_FUNDS", "DFEV,AVES").split(",") if f.strip()),
        sec_user_agent=env.get("ETF_HISTORY_SEC_USER_AGENT", ""),
    )
