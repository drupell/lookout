"""Best-effort 5-digit ZIP -> 2-letter US state code resolver.

Implemented as a ZIP3-prefix lookup table covering all 50 states + DC. The
USPS allocates ZIP codes geographically: the first three digits identify a
sectional center facility, and (with a few well-known exceptions) every ZIP3
prefix maps cleanly to exactly one state.

Used by the incentive-stack feature (federal credit eligibility cares only
about residency state) and any other coarse geography lookups where pulling
in a full ZIP geocoder would be overkill.

Coverage notes:
  - All 50 states + DC are represented as contiguous (start, end) ZIP3 ranges.
  - A handful of multi-state ZIP3 prefixes exist in reality (e.g. 834 is
    shared between ID and a sliver of OR). For our purposes we encode the
    state that owns the majority of population in that prefix.
  - Returns None for non-US ZIPs, malformed input, or ZIP3 prefixes the USPS
    has not assigned to a state (e.g. some military/territory codes).
"""

from __future__ import annotations

# (zip3_start, zip3_end, state_code) — both bounds inclusive. Ranges are
# contiguous, drawn from USPS ZIP3 allocations.
_RANGES: tuple[tuple[int, int, str], ...] = (
    (10, 27, "MA"),  # also 055 for MA military APO/FPO, ignore that edge
    (28, 29, "RI"),
    (30, 38, "NH"),
    (39, 49, "ME"),
    (50, 59, "VT"),
    (60, 69, "CT"),
    (70, 89, "NJ"),
    (100, 102, "NY"),
    (103, 119, "NY"),
    (120, 149, "NY"),
    (150, 196, "PA"),
    (197, 199, "DE"),
    (200, 205, "DC"),
    (206, 219, "MD"),
    (220, 246, "VA"),
    (247, 268, "WV"),
    (270, 289, "NC"),
    (290, 299, "SC"),
    (300, 319, "GA"),
    (320, 339, "FL"),
    (341, 342, "FL"),
    (344, 344, "FL"),
    (346, 347, "FL"),
    (349, 349, "FL"),
    (350, 369, "AL"),
    (370, 385, "TN"),
    (386, 397, "MS"),
    (398, 399, "GA"),
    (400, 427, "KY"),
    (430, 459, "OH"),
    (460, 479, "IN"),
    (480, 499, "MI"),
    (500, 528, "IA"),
    (530, 549, "WI"),
    (550, 567, "MN"),
    (570, 577, "SD"),
    (580, 588, "ND"),
    (590, 599, "MT"),
    (600, 629, "IL"),
    (630, 658, "MO"),
    (660, 679, "KS"),
    (680, 693, "NE"),
    (700, 714, "LA"),
    (716, 729, "AR"),
    (730, 749, "OK"),
    (750, 799, "TX"),
    (800, 816, "CO"),
    (820, 831, "WY"),
    (832, 838, "ID"),
    (840, 847, "UT"),
    (850, 865, "AZ"),
    (870, 884, "NM"),
    (889, 898, "NV"),
    (900, 961, "CA"),
    (970, 979, "OR"),
    (980, 994, "WA"),
    (995, 999, "AK"),
    # Hawaii — non-contiguous range below the mainland blocks
    (967, 968, "HI"),
    # Texas overflow (Amarillo)
    (885, 885, "TX"),
)


def _normalize(zip_code: str) -> int | None:
    """Strip ZIP+4 suffix and whitespace; return the 5-digit prefix as an int."""
    if not isinstance(zip_code, str):
        return None
    s = zip_code.strip()
    if not s:
        return None
    # Accept ZIP+4 ("12345-6789") by taking the part before the dash.
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    if len(s) != 5 or not s.isdigit():
        return None
    return int(s)


def zip_to_state(zip_code: str) -> str | None:
    """Best-effort 5-digit ZIP -> 2-letter US state code.

    Implemented as a ZIP3-prefix lookup table covering all 50 states + DC.
    Returns None for non-US ZIPs or malformed input.

    Accepts ZIP+4 format ("12345-6789") by stripping the suffix.
    """
    n = _normalize(zip_code)
    if n is None:
        return None
    prefix = n // 100  # ZIP3
    for start, end, state in _RANGES:
        if start <= prefix <= end:
            return state
    return None
