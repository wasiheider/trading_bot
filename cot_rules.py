"""Shared smart-money override for every COT read (added 2026-10-23).

One rule, used by cot_generate.py (weekly breakdown PDF), cot_fetch.py
(the /cot dashboard payload, which the weekly trading plan also reads),
so the three can't disagree. User rule 10/23: "if commercials are
extreme bearish how is this bullish" and "Commercials and Retail split
against the lean means LEAN towards Commercial".

The speculator read (Leveraged Funds / Managed Money, confirmed or not by
Non-Commercial) is trend-following. Commercials are the contrarian
smart-money read. When they oppose the speculators, the lean follows the
Commercials:

  CONFLICTED  Commercial IDX at an extreme against the speculator bias
              (>=90 vs. Bearish, <=10 vs. Bullish) or a Smart Money Score of
              30+ against it. Strongest form: lean with Commercials, and
              expect a squeeze/exhaustion against the speculators.
  SPLIT       Commercial bias opposes the speculator bias while Retail sits
              on the speculators' side (e.g. speculators Bullish, Commercial
              Bearish, Retail Bullish -- NQ on 10/20 data). Lean with
              Commercials, at lower conviction than CONFLICTED.

Returns None when neither applies (speculator read stands)."""


def smart_money_check(spec_bias, comm_idx, comm_bias, retail_bias, score):
    if spec_bias not in ("Bullish", "Bearish"):
        return None
    contra = "Bearish" if spec_bias == "Bullish" else "Bullish"
    comm_i = comm_idx if isinstance(comm_idx, int) else None
    if spec_bias == "Bearish":
        extreme = (comm_i is not None and comm_i >= 90) or (score is not None and score >= 30)
    else:
        extreme = (comm_i is not None and comm_i <= 10) or (score is not None and score <= -30)
    if extreme:
        return {"status": "CONFLICTED", "lean": contra,
                "note": f"Speculators {spec_bias} but Commercials at an extreme {contra} "
                        f"(IDX {comm_idx}, Smart Money {score:+d}). Lean {contra} with Commercials; "
                        f"squeeze risk against the speculators." if score is not None else
                        f"Speculators {spec_bias} but Commercials at an extreme {contra} (IDX {comm_idx}). "
                        f"Lean {contra} with Commercials."}
    if comm_bias == contra and retail_bias == spec_bias:
        return {"status": "SPLIT", "lean": contra,
                "note": f"Speculators and Retail {spec_bias}, Commercials {contra} (IDX {comm_idx}). "
                        f"Lean {contra} with Commercials, lower conviction."}
    return None
