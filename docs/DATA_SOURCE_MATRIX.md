# Data source matrix

Verified against primary/provider documentation on 2026-11-02. No market data was downloaded and no metered endpoint was called.

| Source | Intended use | Coverage / timestamps | Auth & limits | Current cost facts | License/storage | Decision |
|---|---|---|---|---|---|---|
| [T‑Bank historical market-data archive](https://developer.tbank.ru/invest/services/history-md/head-history-md) | MOEX GDZ6 raw trades | Daily ZIP/CSV; fields include `TRADE_TS,TICKER_CC,DIRECTION,PRICE,QUANTITY,TRADE_SOURCE,INSTRUMENT_UID`; `TRADE_TS` documented as UTC. GDZ6 October availability is **unverified**. | Public direct URLs are documented; 30 files/min/IP. Do not confuse with the older tokenized candle archive. | No per-file charge documented; operationally candidate for zero-cost P1. | [T‑Bank disclosure](https://www.tbank.ru/invest/disclaimers/basic-information/) permits client use/storage/processing but restricts onward transfer; keep raw/private. Account applicability still needs confirmation. | `PRIMARY_MOEX_CANDIDATE`, gated |
| [MOEX instrument/ISS](https://www.moex.com/ru/derivatives/futures-on-commodity/gdz6) | Contract/session/calendar metadata and cross-check | Official GDZ6 metadata and delayed/current aggregates; free full historical tick coverage through ISS not established. | Public metadata; some products/subscriptions separate. | Free metadata; official historical tick products may be paid. No purchase authorized. | MOEX terms apply; do not redistribute raw exchange data. | `METADATA_ONLY` |
| [CME product/rulebook](https://www.cmegroup.com/content/dam/cmegroup/rulebook/COMEX/1a/113.pdf) | GC specification, sessions, calendar | Official contract facts; not a free complete tick archive. | Public docs; historical downloads use entitlements. | Free metadata. | Reference use only. | `METADATA_ONLY` |
| [CME DataMine](https://www.cmegroup.com/datamine.html) | Possible official GC historical trades | Time & Sales and richer products; a free sample is not proven complete enough. | Account, license and purchased entitlement. | Purchase platform; exact quote intentionally not requested because budget is zero. | CME DataMine license; sample does not imply redistribution rights. | `REJECT_PILOT_COST` |
| [Databento GLBX.MDP3](https://databento.com/docs/datasets/glbx-mdp3), [`trades`](https://databento.com/docs/schemas/trades) | COMEX GCZ6 raw trades | Every trade; `ts_event` matching-engine-received and `ts_recv` capture-server-received in ns representation; sequence and aggressor side available. Coverage claims include COMEX. | API key; metadata/symbology/account calls free. Exact raw-symbol coverage must be checked. | Usage-priced by bytes. [`metadata.get_cost`](https://databento.com/docs/api-historical) is free. [New-user credit is currently $125, expires after six months, one grant/team](https://databento.com/pricing/). Exact one-day/month price and available balance unknown. | Publisher restrictions pass through; verify account/license manager. Raw and raw-equivalent data stay private. | `PRIMARY_COMEX_CANDIDATE`, hard cost gate |

## Verified contract pairing

- [MOEX GOLD‑12.26](https://www.moex.com/ru/derivatives/futures-on-commodity/gdz6): `GDZ6`, cash-settled, USD per troy ounce, lot 1, tick 0.1, listed 2025-12-11 through 2026-12-18; October 2026 is inside its listing period.
- [CME GC](https://www.cmegroup.com/markets/metals/precious/gold.timeAndSales.html): physical Gold Futures, 100 troy ounces, USD per troy ounce, tick USD 0.10. An [official 2026 CME message example](https://www.cmegroup.com/tools-information/lookups/advisories/clearing/files/adding-new-values-to-request-for-cross-indicator.pdf) identifies `GCZ6`, `MMY=20261200`, maturity 2026-12-29, multiplier 100, COMEX.
- Matching quote units make return comparison reasonable. Different contract sizes, settlement and basis forbid interpreting the pair as a ready cash-and-carry trade.

## Session facts

[MOEX announced hours effective 2026-07-14](https://www.moex.com/n101980): 06:50–07:00 MSK auction, 07:00–10:00 morning, 10:00–19:00 main, 19:00–23:50 evening. [CME describes GC](https://www.cmegroup.com/markets/metals/precious/gold.timeAndSales.html) as nearly 24-hour Sunday–Friday with a daily break. P3 must derive overlap from date-specific status/calendars and actual coverage; it must not hard-code a single generic interval.

## Timestamp caveats

- A six-digit fractional example for T‑Bank is a representation, not proof of microsecond clock accuracy. Capture point, clock accuracy, corrections and exact `DIRECTION` semantics remain unverified.
- Databento documents nanosecond representation and matching/capture meanings, but cross-venue inference is still limited by the weaker MOEX timestamp and vendor path.
- Use only `TRADE_SOURCE=EXCHANGE` for primary MOEX data. Treat T‑Bank `DIRECTION` as unknown aggressor side until authoritative semantics are found.

## Mandatory pre-download checks

P1 uses a bounded archive existence probe for one preregistered weekday and records status/headers without retaining market payload until storage/license gates pass. P2 first uses only free metadata/symbology and `metadata.get_cost` for raw symbol `GCZ6`, dataset `GLBX.MDP3`, schema `trades`, exact UTC range. A timeseries/batch request remains prohibited until the estimate, credit balance and user approval are recorded. Prefer one batch job; Databento documents repeat batch downloads for 30 days without another data charge, while repeated streams are re-metered.

## Corrections and additions 2026-11-02 (append-only; ADR-0005)

- MOEX schedule for October 2026 is the regime effective **2026-09-14** (moex.com/n103379): opening auction 06:50–07:00, morning 07:00–09:00, main 09:00–19:00, evening 19:00–23:50 MSK. The 2026-07-14 regime cited under "Session facts" above applies only to 2026-07-14 … 2026-09-13. The opening auction is separate from continuous trading. Trading day = calendar day since 2026-03-23 (moex.com/n98363); weekend sessions belong to the next working day; October had weekend sessions and the 24–25 October weekend session was cancelled (moex.com/n96571).
- T-Bank archive: `https://invest-public-api.tbank.ru/history-trades/{YYYY-MM-DD}?instrumentId=GDZ6_SPBFUT` (identifier = ticker + `_` + class code `SPBFUT`; uniqueness is not claimed globally). One HEAD on 2026-10-06 → 200, `application/octet-stream`, `GDZ6_SPBFUT_2026-10-06.csv.gz` (gzip CSV), no auth. UID not openly published (token-only `FindInstrument`); treated as `OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED`.
- Databento for the current screen: `GLBX.MDP3` `ohlcv-1s` `GCZ6` (raw_symbol), one batch for `[2026-09-01, 2026-11-01)`. `ohlcv-1s` bars exist only for seconds with trades, `ts_event` = interval start, bars built from source `ts_recv` → provider-observation time, not matching-engine event time. Cost unknown until Step A; `DATABENTO_API_KEY` not yet set.

## Unresolved gates

1. Exact T‑Bank `TICKER_CC`, `INSTRUMENT_UID`, GDZ6 daily archive availability and coverage for October 2026.
2. Real T‑Bank timestamp quantum/clock accuracy, `DIRECTION`, duplicates and correction/cancel representation.
3. Complete morning/main/evening and exceptional-session coverage in both sources.
4. GCZ6 and GDZ6 trade counts/liquidity on candidate days.
5. Exact Databento one-day/month cost, current credit and hard zero-charge enforcement.
6. Account-specific local retention and publication rights for normalized/raw-equivalent data.

