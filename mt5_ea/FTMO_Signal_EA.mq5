//+------------------------------------------------------------------+
//| FTMO_Signal_EA.mq5                                                |
//|                                                                    |
//| Polls the trading_bot Railway server's GET /latest-signal for     |
//| entry signals (13 non-micro instruments, same set as the paper    |
//| bot) and trades them on the FTMO MT5 account.                     |
//|                                                                    |
//| Exit management is entirely local, driven by live broker price:   |
//|   - At entry: SL = signal stop_loss, TP = signal tp2 (broker-side |
//|     safety net for both floor and ceiling, works even if this EA  |
//|     is not running).                                              |
//|   - While running: at TP1, close 50% + move SL to breakeven; then |
//|     trail (1.5R -> lock 1R, then trail 0.5R behind peak).         |
//|   - No dependency on the server after the entry signal arrives.   |
//|                                                                    |
//| If the pending (limit/stop) entry order is rejected -- typically  |
//| because price already ran past the level between signal detection |
//| and order placement (poll delay + request round-trip) -- falls    |
//| back to an immediate market order with the same SL/TP, matching   |
//| oanda.py's existing fallback for the paper bot. Confirmed live     |
//| 2026-07-05: a SellLimit on US500.sim was rejected [invalid price]  |
//| with no fallback before this fix was added.                       |
//|                                                                    |
//| Drawdown guardrail (independent of and tighter than FTMO's real   |
//| $500 daily / $1,000 overall limits on this $10K eval account, for |
//| this first live run): blocks new entries and force-closes every   |
//| EA position/order the moment daily loss hits $250 or overall loss |
//| hits $500, then halts permanently until manually restarted.       |
//|                                                                    |
//| SETUP REQUIRED before running:                                    |
//|   1. Tools > Options > Expert Advisors > "Allow WebRequest for    |
//|      listed URL" -> add the Railway URL (see InpServerURL).       |
//|   2. InpSymbolMap_* inputs default to the ".sim" suffix used on   |
//|      the FTMO eval account (confirmed 2026-07-04). Re-check these |
//|      if running on a different account/broker where names differ.|
//|   3. Confirmed on a HEDGING account (each filled order creates a  |
//|      new, independently-tracked position) -- FTMO account 600063135|
//|      verified in Hedge mode 2026-07-04. Re-verify if switching     |
//|      accounts.                                                     |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>

input string InpServerURL      = "https://tradingbot-production-1e5a.up.railway.app/latest-signal";
input int    InpPollSeconds    = 30;
input double InpRiskPercent    = 0.25;      // % of account balance risked per trade ($25 on a $10K account)
input int    InpMagicNumber    = 20260704;
input int    InpMaxSlippagePts = 20;

// FTMO $10K eval account drawdown guardrail -- deliberately tighter than the
// account's real limits ($500 daily / $1,000 overall) for this first live
// run. On breach: all EA positions/pending orders are force-closed and the
// EA halts permanently (requires manual restart to resume).
input double InpDailyLossLimit   = 250;   // USD, resets each trading day
input double InpOverallLossLimit = 500;   // USD, from account inception balance
input int    InpDailyResetHourServer = 22; // server-time hour for 6pm ET/5pm CT daily reset (server clock confirmed ~= UTC 2026-07-04; this is 22 during US daylight time, shifts to 23 if the broker server doesn't observe US DST when it ends)

// Broker symbol name overrides -- FTMO eval account uses a ".sim" suffix on
// every symbol (confirmed 2026-07-04). If this changes on a funded/live
// account, edit these back to match Market Watch.
input string InpSymbolMap_EURUSD = "EURUSD.sim";
input string InpSymbolMap_GBPUSD = "GBPUSD.sim";
input string InpSymbolMap_USDJPY = "USDJPY.sim";
input string InpSymbolMap_EURNZD = "EURNZD.sim";
input string InpSymbolMap_NZDUSD = "NZDUSD.sim";
input string InpSymbolMap_USDCAD = "USDCAD.sim";
input string InpSymbolMap_XAUUSD = "XAUUSD.sim";
input string InpSymbolMap_XAGUSD = "XAGUSD.sim";
input string InpSymbolMap_US100  = "US100.sim";
input string InpSymbolMap_US30   = "US30.sim";
input string InpSymbolMap_US500  = "US500.sim";
input string InpSymbolMap_USOIL  = "USOIL.sim";
input string InpSymbolMap_BTCUSD = "BTCUSD.sim";

// Trailing rule, matches the paper strategy: 1.5R -> lock 1R, then trail 0.5R behind peak
input double InpTrailArmR   = 1.5;
input double InpTrailLockR  = 1.0;
input double InpTrailGapR   = 0.5;

CTrade trade;

#define BOT_SYMBOL_COUNT 13
string BotSymbols[BOT_SYMBOL_COUNT] = {
   "EURUSD", "GBPUSD", "USDJPY", "EURNZD", "NZDUSD", "USDCAD",
   "XAUUSD", "XAGUSD", "US100", "US30", "US500", "USOIL", "BTCUSD"
};

//+------------------------------------------------------------------+
string BrokerSymbolFor(string botSymbol)
  {
   if(botSymbol == "EURUSD") return InpSymbolMap_EURUSD;
   if(botSymbol == "GBPUSD") return InpSymbolMap_GBPUSD;
   if(botSymbol == "USDJPY") return InpSymbolMap_USDJPY;
   if(botSymbol == "EURNZD") return InpSymbolMap_EURNZD;
   if(botSymbol == "NZDUSD") return InpSymbolMap_NZDUSD;
   if(botSymbol == "USDCAD") return InpSymbolMap_USDCAD;
   if(botSymbol == "XAUUSD") return InpSymbolMap_XAUUSD;
   if(botSymbol == "XAGUSD") return InpSymbolMap_XAGUSD;
   if(botSymbol == "US100")  return InpSymbolMap_US100;
   if(botSymbol == "US30")   return InpSymbolMap_US30;
   if(botSymbol == "US500")  return InpSymbolMap_US500;
   if(botSymbol == "USOIL")  return InpSymbolMap_USOIL;
   if(botSymbol == "BTCUSD") return InpSymbolMap_BTCUSD;
   return botSymbol;
  }

//+------------------------------------------------------------------+
//| Minimal JSON extraction -- schema is fixed/known (this server's   |
//| own response shape), so hand-rolled parsing is fine here.         |
//+------------------------------------------------------------------+
string ExtractSymbolBlock(string json, string symbol)
  {
   string key = "\"" + symbol + "\":";
   int pos = StringFind(json, key);
   if(pos < 0) return "";
   int i = pos + StringLen(key);
   int len = StringLen(json);
   while(i < len && StringGetCharacter(json, i) == ' ') i++;
   if(i >= len || StringGetCharacter(json, i) != '{') return ""; // null or missing
   int depth = 0, start = i, endPos = -1;
   for(; i < len; i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(c == '{') depth++;
      else if(c == '}')
        {
         depth--;
         if(depth == 0) { endPos = i; break; }
        }
     }
   if(endPos < 0) return "";
   return StringSubstr(json, start, endPos - start + 1);
  }

string JsonStr(string block, string key)
  {
   string k = "\"" + key + "\":\"";
   int pos = StringFind(block, k);
   if(pos < 0) return "";
   int start = pos + StringLen(k);
   int endPos = StringFind(block, "\"", start);
   if(endPos < 0) return "";
   return StringSubstr(block, start, endPos - start);
  }

double JsonNum(string block, string key)
  {
   string k = "\"" + key + "\":";
   int pos = StringFind(block, k);
   if(pos < 0) return 0;
   int start = pos + StringLen(k);
   int len = StringLen(block);
   int i = start;
   while(i < len)
     {
      ushort c = StringGetCharacter(block, i);
      if((c >= '0' && c <= '9') || c == '-' || c == '.') i++;
      else break;
     }
   return StringToDouble(StringSubstr(block, start, i - start));
  }

long JsonInt(string block, string key) { return (long)JsonNum(block, key); }

//+------------------------------------------------------------------+
//| Last-seen signal ID per symbol -- persisted via GlobalVariables   |
//| (terminal-scoped, survives EA restarts).                          |
//+------------------------------------------------------------------+
long GetLastSeenId(string botSymbol)
  {
   string key = "ftmo_ea_lastid_" + botSymbol;
   if(GlobalVariableCheck(key)) return (long)GlobalVariableGet(key);
   return 0;
  }

void SetLastSeenId(string botSymbol, long id)
  {
   GlobalVariableSet("ftmo_ea_lastid_" + botSymbol, (double)id);
  }

//+------------------------------------------------------------------+
//| Drawdown guardrail -- daily and overall loss limits, independent   |
//| of and tighter than FTMO's actual account rules for this first    |
//| live run. Equity-based (includes floating PNL), checked every     |
//| timer tick, before anything else runs.                             |
//+------------------------------------------------------------------+
bool IsHalted()
  {
   return GlobalVariableCheck("ftmo_ea_halted") && GlobalVariableGet("ftmo_ea_halted") == 1;
  }

void CheckDailyReset()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   int todayMarker = t.year * 1000 + t.day_of_year;
   int tradingDayMarker = (t.hour < InpDailyResetHourServer) ? todayMarker - 1 : todayMarker;

   int lastMarker = GlobalVariableCheck("ftmo_ea_trading_day") ? (int)GlobalVariableGet("ftmo_ea_trading_day") : -1;
   if(tradingDayMarker != lastMarker)
     {
      GlobalVariableSet("ftmo_ea_trading_day", tradingDayMarker);
      GlobalVariableSet("ftmo_ea_daily_start_equity", AccountInfoDouble(ACCOUNT_EQUITY));
      Print("New trading day (marker=", tradingDayMarker, "), daily start equity=", AccountInfoDouble(ACCOUNT_EQUITY));
     }
  }

bool DrawdownBreached()
  {
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   double dailyStart = GlobalVariableCheck("ftmo_ea_daily_start_equity")
                        ? GlobalVariableGet("ftmo_ea_daily_start_equity") : equity;
   double dailyLoss = dailyStart - equity;
   if(dailyLoss >= InpDailyLossLimit)
     {
      Print("*** DAILY LOSS LIMIT BREACHED: $", dailyLoss, " >= $", InpDailyLossLimit, " ***");
      return true;
     }

   if(!GlobalVariableCheck("ftmo_ea_start_balance"))
      GlobalVariableSet("ftmo_ea_start_balance", AccountInfoDouble(ACCOUNT_BALANCE));
   double overallStart = GlobalVariableGet("ftmo_ea_start_balance");
   double overallLoss = overallStart - equity;
   if(overallLoss >= InpOverallLossLimit)
     {
      Print("*** OVERALL LOSS LIMIT BREACHED: $", overallLoss, " >= $", InpOverallLossLimit, " ***");
      return true;
     }

   return false;
  }

void CloseAllAndHalt()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         trade.PositionClose(ticket);
     }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(OrderSelect(ticket) && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
         trade.OrderDelete(ticket);
     }
   GlobalVariableSet("ftmo_ea_halted", 1);
   Print("=== DRAWDOWN LIMIT BREACHED: ALL POSITIONS/ORDERS CLOSED, EA HALTED. Manual restart required. ===");
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(InpPollSeconds);
   Print("FTMO_Signal_EA initialized. Polling ", InpServerURL, " every ", InpPollSeconds, "s.");
   Print("Reminder: URL must be whitelisted under Tools > Options > Expert Advisors > Allow WebRequest.");
   if(IsHalted())
      Print("*** EA IS HALTED from a prior drawdown breach. Remove and re-add the EA (or reset ftmo_ea_halted global variable) to resume. ***");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
  {
   if(IsHalted()) return; // permanently stopped after a breach -- requires manual intervention

   CheckDailyReset();
   if(DrawdownBreached())
     {
      CloseAllAndHalt();
      return;
     }

   PollSignals();
   ManageOpenPositions();
  }

//+------------------------------------------------------------------+
//| Poll the server and act on any new signal per instrument          |
//+------------------------------------------------------------------+
void PollSignals()
  {
   string headers = "";
   char post[];
   char result[];
   string resultHeaders;
   ResetLastError();
   int res = WebRequest("GET", InpServerURL, headers, 5000, post, result, resultHeaders);
   if(res == -1)
     {
      Print("WebRequest failed, error ", GetLastError(),
            " -- is the URL whitelisted in Tools > Options > Expert Advisors?");
      return;
     }
   if(res != 200)
     {
      Print("Server returned HTTP ", res);
      return;
     }
   string json = CharArrayToString(result);

   for(int s = 0; s < BOT_SYMBOL_COUNT; s++)
     {
      string botSymbol = BotSymbols[s];
      string block = ExtractSymbolBlock(json, botSymbol);
      if(block == "") continue; // null / no recent signal

      long signalId = JsonInt(block, "signal_id");
      if(signalId <= GetLastSeenId(botSymbol)) continue; // already handled

      SetLastSeenId(botSymbol, signalId); // mark seen immediately, even if entry fails below
      TryEnter(botSymbol, block);
     }
  }

//+------------------------------------------------------------------+
//| Attempt to place an entry for a new signal                        |
//+------------------------------------------------------------------+
void TryEnter(string botSymbol, string block)
  {
   string brokerSymbol = BrokerSymbolFor(botSymbol);
   if(!SymbolSelect(brokerSymbol, true))
     {
      Print("Cannot select symbol ", brokerSymbol, " (bot symbol ", botSymbol,
            ") -- check InpSymbolMap_* inputs match your broker's Market Watch names.");
      return;
     }

   if(HasOpenExposure(brokerSymbol))
     {
      Print("Skipping ", botSymbol, " (", brokerSymbol, "): exposure already open -- one position/pending order per instrument.");
      return;
     }

   string direction = JsonStr(block, "direction");
   string setup      = JsonStr(block, "setup");
   double entryPrice = JsonNum(block, "entry_price");
   double stopLoss   = JsonNum(block, "stop_loss");
   double tp1        = JsonNum(block, "tp1");
   double tp2        = JsonNum(block, "tp2");
   if(entryPrice <= 0 || stopLoss <= 0) { Print("Bad signal data for ", botSymbol); return; }

   bool isLong = (direction == "LONG");
   double slDistance = MathAbs(entryPrice - stopLoss);
   if(slDistance <= 0) { Print("Zero SL distance for ", botSymbol); return; }

   double lots = ComputeLotSize(botSymbol, brokerSymbol, slDistance);
   if(lots <= 0) { Print("Computed zero lot size for ", botSymbol); return; }

   int digits = (int)SymbolInfoInteger(brokerSymbol, SYMBOL_DIGITS);
   entryPrice = NormalizeDouble(entryPrice, digits);
   stopLoss   = NormalizeDouble(stopLoss, digits);
   tp2        = NormalizeDouble(tp2, digits);

   datetime expiration = DayEndUTC();
   string comment = "ftmo_ea_" + setup;
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpMaxSlippagePts);

   bool ok;
   if(setup == "box_break")
      ok = isLong
         ? trade.BuyStop(lots, entryPrice, brokerSymbol, stopLoss, tp2, ORDER_TIME_DAY, expiration, comment)
         : trade.SellStop(lots, entryPrice, brokerSymbol, stopLoss, tp2, ORDER_TIME_DAY, expiration, comment);
   else
      ok = isLong
         ? trade.BuyLimit(lots, entryPrice, brokerSymbol, stopLoss, tp2, ORDER_TIME_DAY, expiration, comment)
         : trade.SellLimit(lots, entryPrice, brokerSymbol, stopLoss, tp2, ORDER_TIME_DAY, expiration, comment);

   if(!ok)
     {
      // Pending order rejected -- most likely price already ran past the level
      // by the time this executed (signal detected -> poll delay -> request
      // round-trip). Same fallback oanda.py already uses for the paper bot:
      // retry immediately as a market order.
      //
      // Re-anchor SL/TP to the CURRENT market price rather than reusing the
      // original (now-stale) absolute levels -- if price ran far enough to
      // reject the pending order, the original tp2 can end up on the wrong
      // side of the new fill price, which MT5 rejects as [Invalid stops]
      // (found live, 2026-07-23: US500 SellLimit rejected [Invalid price],
      // market fallback then rejected [Invalid stops] using the stale tp2).
      // Preserve the original risk distance and reward distance so position
      // sizing (computed from slDistance) and R:R stay correct.
      Print("Pending order failed for ", botSymbol, ": ", trade.ResultRetcodeDescription(),
            " -- falling back to market order.");
      double tpDistance   = MathAbs(tp2 - entryPrice);
      double currentPrice = isLong
         ? SymbolInfoDouble(brokerSymbol, SYMBOL_ASK)
         : SymbolInfoDouble(brokerSymbol, SYMBOL_BID);
      double marketSL = NormalizeDouble(isLong ? currentPrice - slDistance : currentPrice + slDistance, digits);
      double marketTP = NormalizeDouble(isLong ? currentPrice + tpDistance : currentPrice - tpDistance, digits);
      ok = isLong
         ? trade.Buy(lots, brokerSymbol, 0, marketSL, marketTP, comment)
         : trade.Sell(lots, brokerSymbol, 0, marketSL, marketTP, comment);
      if(!ok)
        {
         Print("Market order fallback also failed for ", botSymbol, ": ", trade.ResultRetcodeDescription());
         return;
        }
     }

   ulong orderTicket = trade.ResultOrder();
   GlobalVariableSet("ftmo_ea_tp1_" + (string)orderTicket, tp1);
   Print("Placed ", direction, " ", setup, " order for ", botSymbol, " (", brokerSymbol,
         ") @ ", entryPrice, " lots=", lots, " ticket=", orderTicket);
  }

//+------------------------------------------------------------------+
//| True if we already have an open position or pending order on     |
//| this symbol (enforces one position per instrument).               |
//+------------------------------------------------------------------+
bool HasOpenExposure(string brokerSymbol)
  {
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket)
         && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber
         && PositionGetString(POSITION_SYMBOL) == brokerSymbol)
        {
         Print("HasOpenExposure(", brokerSymbol, "): blocked by open POSITION ticket ", ticket);
         return true;
        }
     }
   for(int i = 0; i < OrdersTotal(); i++)
     {
      ulong ticket = OrderGetTicket(i);
      if(OrderSelect(ticket)
         && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber
         && OrderGetString(ORDER_SYMBOL) == brokerSymbol)
        {
         Print("HasOpenExposure(", brokerSymbol, "): blocked by pending ORDER ticket ", ticket,
               " type=", EnumToString((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE)));
         return true;
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Generic risk-based position sizing -- uses the broker's own       |
//| tick value/size for the symbol, so it works uniformly across      |
//| forex, indices, metals, oil and crypto without per-instrument     |
//| hardcoded pip values.                                             |
//+------------------------------------------------------------------+
double ComputeLotSize(string botSymbol, string brokerSymbol, double slDistance)
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   // Risk bump, added 2026-08-25 (user call, matching the paper bot's Pine
   // Script change same day): US100 (all trades) and US500 both get 0.5%
   // instead of the 0.25% baseline. No session check needed for US500 here --
   // the Pine Script now only ever fires a US500 signal during NY AM
   // (8am-12pm CT), so by the time a US500 signal reaches this EA via
   // /latest-signal, it is already guaranteed to be an NY-AM signal.
   double riskPct   = (botSymbol == "US100" || botSymbol == "US500") ? InpRiskPercent * 2.0 : InpRiskPercent;
   double riskMoney = balance * riskPct / 100.0;

   double tickValue = SymbolInfoDouble(brokerSymbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(brokerSymbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0 || tickSize <= 0) return 0;

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0) return 0;

   double lots = riskMoney / lossPerLot;

   double volStep = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_STEP);
   double volMin  = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_MIN);
   double volMax  = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_MAX);
   lots = MathFloor(lots / volStep) * volStep;

   // If the risk-sized lot is below the broker's minimum tradeable size,
   // do NOT force it up to volMin -- that silently risks more than
   // InpRiskPercent (found on XAGUSD, 2026-07-22: forced 0.01 min lot risked
   // well more than the intended $25 on the $10K Phase 2 account). Skip the
   // signal instead; a missed trade is preferable to an oversized one.
   if(lots < volMin)
     {
      double minLotRisk = volMin * lossPerLot;
      Print("Skipping ", brokerSymbol, ": broker minimum lot ", volMin,
            " would risk $", DoubleToString(minLotRisk, 2), " vs $",
            DoubleToString(riskMoney, 2), " budget -- signal skipped, not oversized.");
      return 0;
     }

   lots = MathMin(volMax, lots);
   return NormalizeDouble(lots, 2);
  }

datetime DayEndUTC()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   t.hour = 23; t.min = 59; t.sec = 0;
   return StructToTime(t);
  }

//+------------------------------------------------------------------+
//| Local exit management: TP1 partial close + breakeven + trailing.  |
//| Runs every OnTimer(); never depends on the server.                |
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      string sym   = PositionGetString(POSITION_SYMBOL);
      bool isLong  = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL = PositionGetDouble(POSITION_SL);
      double curTP = PositionGetDouble(POSITION_TP);
      double vol   = PositionGetDouble(POSITION_VOLUME);
      double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);
      double exitPrice = isLong ? bid : ask; // price we'd realize on a close right now

      string tp1Key = "ftmo_ea_tp1_" + (string)ticket;
      if(!GlobalVariableCheck(tp1Key)) continue; // not one of ours / not yet initialized
      double tp1 = GlobalVariableGet(tp1Key);

      string phaseKey = "ftmo_ea_phase_" + (string)ticket;
      string origSlKey = "ftmo_ea_origsl_" + (string)ticket;
      string peakKey   = "ftmo_ea_peak_" + (string)ticket;

      int phase = GlobalVariableCheck(phaseKey) ? (int)GlobalVariableGet(phaseKey) : 0;
      double origSL = GlobalVariableCheck(origSlKey) ? GlobalVariableGet(origSlKey) : curSL;
      if(!GlobalVariableCheck(origSlKey)) GlobalVariableSet(origSlKey, origSL);

      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

      if(phase == 0)
        {
         bool tp1Hit = isLong ? (bid >= tp1) : (ask <= tp1);
         if(!tp1Hit) continue;

         double closeVol = NormalizeDouble(vol / 2.0, 2);
         double volMin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
         if(closeVol >= volMin && closeVol < vol)
           {
            trade.PositionClosePartial(ticket, closeVol);
           }

         double breakeven = NormalizeDouble(entry, digits);
         trade.PositionModify(ticket, breakeven, curTP); // keep tp2 as broker TP unchanged

         GlobalVariableSet(phaseKey, 1);
         GlobalVariableSet(peakKey, exitPrice);
         continue;
        }

      // phase 1: trailing
      double peak = GlobalVariableCheck(peakKey) ? GlobalVariableGet(peakKey) : exitPrice;
      if(isLong && exitPrice > peak) { peak = exitPrice; GlobalVariableSet(peakKey, peak); }
      if(!isLong && exitPrice < peak) { peak = exitPrice; GlobalVariableSet(peakKey, peak); }

      double R = MathAbs(entry - origSL);
      if(R <= 0) continue;
      double profitR = isLong ? (peak - entry) / R : (entry - peak) / R;

      double newSL = curSL;
      if(profitR >= InpTrailArmR)
         newSL = isLong ? peak - InpTrailGapR * R : peak + InpTrailGapR * R;
      else if(profitR >= InpTrailLockR)
         newSL = isLong ? entry + InpTrailLockR * R : entry - InpTrailLockR * R;

      newSL = NormalizeDouble(newSL, digits);
      bool improves = isLong ? (newSL > curSL) : (newSL < curSL);
      if(improves) trade.PositionModify(ticket, newSL, curTP);
     }
  }
