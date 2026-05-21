# =============================================================================
# VALIDATOR.PY — CLAUDE AI SIGNAL VALIDATION
# =============================================================================
# Sends each incoming Wyckoff BOS signal to Claude for validation.
# Claude checks strategy rules, session context, R:R, and risk before
# returning a structured JSON decision: approve/reject + reasoning.
# =============================================================================

import json
import logging
import anthropic
import config

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# ANTHROPIC CLIENT
# -----------------------------------------------------------------------------
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# -----------------------------------------------------------------------------
# SYSTEM PROMPTS — ONE PER ACCOUNT
# -----------------------------------------------------------------------------

APEX_SYSTEM_PROMPT = """
You are a strict risk validator for an intraday futures trading bot.

ACCOUNT: Apex Trader Funding (funded futures account)
STRATEGY: Wyckoff range trading — Break of Structure (BOS) reversal entries
MARKETS: ES, NQ, MES, MNQ, CL, NG, GC, SI
STYLE: Intraday — entries only during London (1am-4am CT) and NY (8am-11am CT) sessions

STRATEGY RULES:
1. A valid range requires price to touch the 50% midpoint at least twice and return to mean
2. BOS is confirmed by 2 consecutive 5-minute candle closes beyond the range boundary
3. Entry is taken on the reversal back into the range after BOS
4. Stop loss sits beyond the BOS extreme wick
5. TP1 = 50% midpoint of the range
6. TP2 = opposite end of the range
7. If R:R to TP1 >= 1:3 → full close at TP1
8. If R:R to TP1 = 1:2 → close 50% at TP1, move SL to breakeven, run to TP2

RISK RULES:
- Maximum 1% risk per trade on account balance
- Only trade during active sessions (London or NY)
- No trades during high-impact news events
- Hard position close at 2:55pm CT daily
- Minimum R:R of 1:2 to TP1 required — reject anything below

VALIDATION CRITERIA — check all of these:
1. Is the R:R to TP1 at least 1:2? If not, REJECT immediately
2. Does the direction make sense for a BOS reversal? (BOS above range = SHORT, BOS below = LONG)
3. Is TP1 correctly set at the range midpoint?
4. Is TP2 correctly set at the opposite range boundary?
5. Is the stop loss logically placed beyond the BOS extreme?
6. Does the range appear valid? (mid is halfway between high and low)
7. Any concerns about the setup quality?

RESPONSE FORMAT — respond ONLY with valid JSON, no other text:
{
  "approve": true or false,
  "confidence": 0-100,
  "action": "FULL_CLOSE_TP1" or "PARTIAL_CLOSE_TP1" or "REJECT",
  "reasoning": "Clear explanation of your decision in 2-3 sentences",
  "warnings": ["Any concerns or flags as a list — empty list if none"],
  "adjusted_sl": null or float (if you recommend a tighter SL),
  "risk_notes": "Brief note on position sizing or risk"
}
"""

FTMO_SYSTEM_PROMPT = """
You are a strict risk validator for a swing forex and metals trading bot.

ACCOUNT: FTMO funded account — $100,000
STRATEGY: Wyckoff range trading — Break of Structure (BOS) reversal entries
MARKETS: EURUSD, GBPUSD, XAUUSD, XAGUSD
STYLE: Swing trading — multi-day holds, 4H/Daily chart BOS confirmation

STRATEGY RULES:
1. A valid range requires price to touch the 50% midpoint at least twice and return to mean
2. BOS is confirmed by 2 consecutive 4-hour candle closes beyond the range boundary
3. Entry is taken on the reversal back into the range after BOS
4. Stop loss sits beyond the BOS extreme wick on the 4H chart
5. TP1 = 50% midpoint of the range
6. TP2 = opposite end of the range
7. If R:R to TP1 >= 1:3 → full close at TP1
8. If R:R to TP1 = 1:2 → close 50% at TP1, move SL to breakeven, run to TP2
9. Positions held multi-day — no force close unless news event on that pair

RISK RULES:
- Maximum 1% risk per trade ($1,000 on $100k account)
- Close positions 30 minutes before high-impact news on the traded pair
- Minimum R:R of 1:2 to TP1 required — reject anything below
- FTMO daily loss limit: 5% ($5,000) — reject if approaching limit
- FTMO max drawdown: 10% ($10,000) — reject if approaching limit
- Minimum R:R of 1:2 to TP2 preferred for swing trades

VALIDATION CRITERIA — check all of these:
1. Is the R:R to TP1 at least 1:2? If not, REJECT immediately
2. Does the direction make sense for a BOS reversal? (BOS above range = SHORT, BOS below = LONG)
3. Is TP1 correctly set at the range midpoint?
4. Is TP2 correctly set at the opposite range boundary?
5. Is the stop loss logically placed beyond the BOS extreme?
6. Does the range appear valid? (mid is halfway between high and low)
7. Is the overall R:R acceptable for a multi-day swing trade?
8. Any concerns about the setup quality?

RESPONSE FORMAT — respond ONLY with valid JSON, no other text:
{
  "approve": true or false,
  "confidence": 0-100,
  "action": "FULL_CLOSE_TP1" or "PARTIAL_CLOSE_TP1" or "REJECT",
  "reasoning": "Clear explanation of your decision in 2-3 sentences",
  "warnings": ["Any concerns or flags as a list — empty list if none"],
  "adjusted_sl": null or float (if you recommend a tighter SL),
  "risk_notes": "Brief note on position sizing or risk"
}
"""

# -----------------------------------------------------------------------------
# MAIN VALIDATION FUNCTION
# -----------------------------------------------------------------------------

async def validate_signal(signal) -> dict:
    """
    Send a trading signal to Claude for validation.
    Returns a structured dict with approve/reject decision and reasoning.
    
    Args:
        signal: SignalPayload object from server.py
    
    Returns:
        dict with keys: approve, confidence, action, reasoning, warnings,
                        adjusted_sl, risk_notes
    """

    # Pick the correct system prompt
    # Pine Script sends account="TPT"; APEX is a legacy label — handle both
    system_prompt = APEX_SYSTEM_PROMPT if signal.account in ("APEX", "TPT") else FTMO_SYSTEM_PROMPT

    # Build the user message with all signal details
    user_message = f"""
Please validate this Wyckoff BOS trading signal:

SIGNAL DETAILS:
- Account    : {signal.account}
- Symbol     : {signal.symbol}
- Direction  : {signal.direction}
- Timeframe  : {signal.timeframe} minutes
- Session    : {signal.session}
- Bar time   : {signal.bar_time}

RANGE STRUCTURE:
- Range high : {signal.range_high}
- Range mid  : {signal.range_mid}  ← TP1 target
- Range low  : {signal.range_low}
- BOS extreme: {signal.bos_extreme}  ← stop loss reference

TRADE LEVELS:
- Entry price: {signal.entry_price}
- Stop loss  : {signal.stop_loss}
- TP1        : {signal.tp1}  (50% range midpoint)
- TP2        : {signal.tp2}  (opposite range end)
- R:R to TP1 : {signal.rr_to_tp1:.2f}

RANGE VALIDITY CHECK:
- Range size : {abs(signal.range_high - signal.range_low):.4f}
- Risk (entry to SL): {abs(signal.entry_price - signal.stop_loss):.4f}
- Reward to TP1: {abs(signal.entry_price - signal.tp1):.4f}
- Reward to TP2: {abs(signal.entry_price - signal.tp2):.4f}

Please validate this signal according to the strategy rules and respond with JSON only.
"""

    log.info(f"[{signal.account}] Sending signal to Claude for validation...")

    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        # Extract the response text
        raw_response = response.content[0].text.strip()
        log.info(f"[{signal.account}] Claude raw response: {raw_response}")

        # Parse JSON response
        # Strip markdown code blocks if Claude wraps in ```json
        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]

        decision = json.loads(raw_response.strip())

        # Log the decision clearly
        status = "APPROVED [OK]" if decision.get("approve") else "REJECTED [NO]"
        log.info(
            f"[{signal.account}] Claude decision: {status} | "
            f"Confidence: {decision.get('confidence')}% | "
            f"Action: {decision.get('action')}"
        )
        log.info(f"[{signal.account}] Reasoning: {decision.get('reasoning')}")

        if decision.get("warnings"):
            for w in decision["warnings"]:
                log.warning(f"[{signal.account}] Warning: {w}")

        return decision

    except json.JSONDecodeError as e:
        log.error(f"[{signal.account}] Failed to parse Claude response as JSON: {e}")
        log.error(f"[{signal.account}] Raw response was: {raw_response}")
        # Return a safe rejection if Claude response can't be parsed
        return {
            "approve": False,
            "confidence": 0,
            "action": "REJECT",
            "reasoning": "Validator error — could not parse Claude response. Signal rejected for safety.",
            "warnings": ["JSON parse error in validator"],
            "adjusted_sl": None,
            "risk_notes": "Signal rejected due to validator error"
        }

    except anthropic.APIError as e:
        log.error(f"[{signal.account}] Anthropic API error: {e}")
        return {
            "approve": False,
            "confidence": 0,
            "action": "REJECT",
            "reasoning": f"Claude API error: {str(e)}. Signal rejected for safety.",
            "warnings": ["Claude API unavailable"],
            "adjusted_sl": None,
            "risk_notes": "Signal rejected due to API error"
        }

    except Exception as e:
        log.error(f"[{signal.account}] Unexpected error in validator: {e}")
        return {
            "approve": False,
            "confidence": 0,
            "action": "REJECT",
            "reasoning": f"Unexpected error: {str(e)}. Signal rejected for safety.",
            "warnings": [f"Unexpected error: {str(e)}"],
            "adjusted_sl": None,
            "risk_notes": "Signal rejected due to unexpected error"
        }
