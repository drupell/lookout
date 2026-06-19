"""Federal EV credit eligibility logic (IRS Sections 30D and 45W).

Section 30D is the consumer-facing direct-purchase tax credit. It carries
adjusted-gross-income caps per filing status (single $150k, head-of-household
$225k, married-filing-jointly $300k). Buyers above the cap are excluded from
the direct credit but may still capture the full $7,500 via a lease, because
Section 45W (commercial clean-vehicle credit) flows to the leasing company
and is conventionally passed through as a price reduction. There are no
income caps on 45W.

This module is intentionally narrow: given a coarse income bracket and an
optional filing status, return a structured eligibility verdict. It never
raises — the worst case is to report "doesn't qualify directly, lease path
remains available," which is the safe descriptive answer.
"""

from __future__ import annotations

from typing import Any

from src.config.loader import IncomeTier


def qualifies_for_30d(
    income_tier: IncomeTier,
    filing_status: str = "single",
) -> dict[str, Any]:
    """Return {qualifies, reason, alt_path_via_lease}.

    Per IRS Section 30D (for direct purchase):
      - single: $150k
      - HOH:    $225k
      - MFJ:    $300k

    income_tier ladder maps:
      UNDER_75K        -> qualifies (below all caps)
      UNDER_150K       -> qualifies (below single-filer cap, so all statuses)
      UNDER_300K       -> qualifies depending on filing status (we default
                          to "safe assumption: MFJ" for the under_300k bracket;
                          treats as qualifying since most users at this tier
                          file MFJ; conservative for direct-purchase messaging)
      ABOVE_300K       -> does NOT qualify for direct; lease 45W pass-through
                          remains available
      PREFER_NOT_TO_SAY -> assume worst case (does NOT qualify direct);
                          alt_path_via_lease = True

    The `alt_path_via_lease` flag is True whenever the user could still capture
    the credit via a Section 45W lease pass-through — i.e. whenever direct
    qualification is denied OR unknown. It's False when direct qualification
    is already affirmative (no need for the alt path).

    Returns a dict, never raises. Unknown enum values fall through to the
    conservative "treat as above the cap" branch.
    """
    status = (filing_status or "single").strip().lower()

    if income_tier == IncomeTier.UNDER_75K:
        return {
            "qualifies": True,
            "reason": (
                "Household income under $75k is below every Section 30D filing-status "
                "cap ($150k single / $225k HOH / $300k MFJ)."
            ),
            "alt_path_via_lease": False,
        }

    if income_tier == IncomeTier.UNDER_150K:
        return {
            "qualifies": True,
            "reason": (
                "Household income under $150k is at or below the single-filer Section "
                "30D cap, so all filing statuses qualify."
            ),
            "alt_path_via_lease": False,
        }

    if income_tier == IncomeTier.UNDER_300K:
        # The bracket spans $150k-$300k. Whether direct purchase qualifies
        # depends on filing status.
        if status in ("mfj", "married", "married_filing_jointly", "joint"):
            return {
                "qualifies": True,
                "reason": (
                    "Household income under $300k qualifies for the Section 30D direct "
                    "credit when filing jointly (cap: $300k MFJ)."
                ),
                "alt_path_via_lease": False,
            }
        if status in ("hoh", "head_of_household", "head-of-household"):
            # $150k-$225k qualifies; $225k-$300k does not. We don't know which
            # half of the bracket — be conservative.
            return {
                "qualifies": False,
                "reason": (
                    "Household income may exceed the $225k head-of-household cap for "
                    "Section 30D direct purchase. Lease (Section 45W) pass-through "
                    "remains available."
                ),
                "alt_path_via_lease": True,
            }
        # single (or unspecified) — $150k-$300k is above the $150k single cap
        return {
            "qualifies": False,
            "reason": (
                "Household income exceeds the $150k single-filer cap for Section 30D "
                "direct purchase. Lease (Section 45W) pass-through remains available."
            ),
            "alt_path_via_lease": True,
        }

    if income_tier == IncomeTier.ABOVE_300K:
        return {
            "qualifies": False,
            "reason": (
                "Household income exceeds the $300k MFJ cap — the highest Section 30D "
                "cap across filing statuses. The lease (Section 45W) pass-through has "
                "no income limit and remains available."
            ),
            "alt_path_via_lease": True,
        }

    # PREFER_NOT_TO_SAY or any unknown value — safe worst case.
    return {
        "qualifies": False,
        "reason": (
            "Income bracket not provided; assuming the conservative case for "
            "Section 30D direct purchase. The lease (Section 45W) pass-through "
            "has no income limit and remains available."
        ),
        "alt_path_via_lease": True,
    }
