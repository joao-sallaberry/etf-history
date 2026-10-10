# Share type in a Brazilian ISIN (chars 6:11, or 6:10 for units) -> B3 ticker suffix.
BR_SHARE_SUFFIX = {
    "ACNOR": "3",
    "ACNPR": "4",
    "ACNPA": "5",
    "ACNPB": "6",
    "ACNPC": "7",
    "ACNPD": "8",
    "CDAM": "11",
}


def br_ticker(isin: str) -> str:
    """Best-effort B3 ticker from a Brazilian ISIN (BRPETRACNPR6 -> PETR4); '' when unknown."""
    if not isin.startswith("BR") or len(isin) != 12:
        return ""
    kind = isin[6:11]
    suffix = BR_SHARE_SUFFIX.get(kind) or BR_SHARE_SUFFIX.get(kind[:4], "")
    return isin[2:6] + suffix if suffix else ""
