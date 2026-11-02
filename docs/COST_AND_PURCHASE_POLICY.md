# Cost and purchase policy

## Default

The authorized cash budget is exactly **0 RUB and 0 USD charged**. No agent may purchase data, start a paid subscription, activate exchange packages, open/fund brokerage accounts, or issue orders.

## Download gate

Before every potentially metered request, record:

1. source, dataset, schema, symbols, UTC interval and estimated bytes;
2. provider cost estimate obtained without downloading the payload;
3. current free-credit/billing facts visible to the user;
4. cache/ledger check proving the identical payload is not already present;
5. decision `FREE_CONFIRMED` or `STOP_USER_DECISION`.

Only `FREE_CONFIRMED` permits the exact bounded request. An estimate of zero or safely covered credit is required, but credit is not permission to create a paid obligation. If the provider cannot enforce a hard zero-charge boundary, stop.

## Ledger fields

`request_id`, `provider`, `dataset`, `schema`, `symbols`, `start_utc`, `end_utc`, `estimated_bytes`, `estimated_cost_currency`, `estimated_cost`, `credit_checked_at`, `decision`, `raw_manifest_id`, `actual_cost_if_known`, `operator`.

Tokens and account balances must be redacted. Any non-zero proposal is a user decision documented before the request.

