"""The one COT rule, shared by every COT output (rewritten 2026-10-23).

Used by cot_generate.py (weekly breakdown PDF) and cot_fetch.py (the /cot
dashboard payload, which the weekly trading plan also reads), so they
always agree.

User rule 10/23: "we never follow retail or hedge funds. we look for
extremes when Commercials are on one side and hedge funds and/or retail
are towards other extreme." Thresholds (user, 10/23): 80 / 20 COT Index.
"Hedge funds" = either speculator source: Leveraged Funds / Managed Money
(CFTC TFF / Disaggregated) or Non-Commercial (CFTC Legacy, the same report
the Commercial index comes from).

  Bullish setup: Commercial IDX >= 80 and hedge funds and/or retail <= 20.
  Bearish setup: Commercial IDX <= 20 and hedge funds and/or retail >= 80.
  Anything else: no COT edge (the speculators' direction is never a lean).

Returns {"signal", "comm_idx", "opposite", "note"} or None."""

EXTREME_HI = 80
EXTREME_LO = 20


def cot_setup(comm_idx, hf_idx, nc_idx, retail_idx):
    if not isinstance(comm_idx, int):
        return None
    if comm_idx >= EXTREME_HI:
        signal, hit = "Bullish", lambda v: isinstance(v, int) and v <= EXTREME_LO
    elif comm_idx <= EXTREME_LO:
        signal, hit = "Bearish", lambda v: isinstance(v, int) and v >= EXTREME_HI
    else:
        return None
    opposite = [f"{name} IDX {v}" for name, v in (("Hedge funds", hf_idx), ("Non-Commercial", nc_idx),
                                                  ("Retail", retail_idx)) if hit(v)]
    if not opposite:
        return None
    side = "long" if signal == "Bullish" else "short"
    other = "short" if signal == "Bullish" else "long"
    return {"signal": signal, "comm_idx": comm_idx, "opposite": opposite,
            "note": f"Commercials at an extreme {side} (IDX {comm_idx}) vs. {', '.join(opposite)} at the "
                    f"opposite extreme ({other}). Lean {signal} with Commercials."}


def commercials_waiting(comm_idx):
    """Commercials at an extreme but nobody stretched the other way yet --
    a watch item, not a lean."""
    if isinstance(comm_idx, int) and (comm_idx >= EXTREME_HI or comm_idx <= EXTREME_LO):
        return "long" if comm_idx >= EXTREME_HI else "short"
    return None
