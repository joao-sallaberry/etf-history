-- One row per downloaded holdings file.
create table if not exists snapshot (
    fund        text not null,
    as_of       text not null,           -- YYYY-MM-DD, the `date` column inside the file
    source      text not null default 'daily', -- daily (fund's own daily file) | archive (Wayback) | nport (SEC)
    fetched_at  text not null,           -- UTC ISO timestamp
    file_path   text,                    -- raw file, relative to the data dir
    sha256      text not null,
    row_count   integer not null,
    primary key (fund, as_of, source)
);

-- Days the source has no file for (holidays) or served a file for another date; not requested again.
create table if not exists missing_day (
    fund        text not null,
    day         text not null,
    reason      text not null,           -- '404' or 'date:<date found in the file>'
    checked_at  text not null,
    primary key (fund, day)
);

-- Brazilian lines of a holdings file (B3-listed securities and ADRs of Brazilian companies); the raw file in
-- data/raw keeps everything.
create table if not exists holding (
    fund        text not null,
    as_of       text not null,
    source      text not null default 'daily',
    key         text not null,           -- isin, else sedol/identifier, else description (cash, FX, accruals)
    isin        text,
    ticker      text,                    -- B3-style for Brazil (PETR4); first token of the source ticker otherwise
    source_ticker text,                  -- as in the file ("PETR4 BZ")
    name        text,
    country     text,                    -- issuer country (BR, or the foreign issuer of a BDR; seeds/securities.csv)
    company     text,                    -- B3 issuer code (PETR) after seeds/company_aliases.csv; groups classes/ADRs
    kind        text,                    -- share | unit | right | adr (BDRs are not stored)
    share_ratio real,                    -- local shares per unit of this security (PBR ADR = 2); see seeds/
    sedol       text,
    shares      real,
    value_usd   real,
    value_local real,                    -- local-currency value; NULL in older files (see CLAUDE.md)
    weight      real,                    -- fraction of the fund (DFEV: of net assets; AVES: of Σ market value)
    primary key (fund, as_of, source, key)
) without rowid;

create index if not exists holding_isin on holding (isin, as_of);
create index if not exists holding_company on holding (fund, company, as_of);

-- Key/value state (e.g. the hash of the seeds the stored rows were built with).
create table if not exists meta (
    key         text primary key,
    value       text
);
