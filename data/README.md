# data

written by the [daily workflow](../.github/workflows/daily.yml) at 00:33 utc.

`daily.csv`: one row per asset and market per utc date, for the default assets (usdc, usdt, dai, usds,
usde, pyusd, weth) on aave v3, spark and compound v3. columns:

- `date_utc`, `block` (every number in a row is read at this block)
- `asset` (the token's own `symbol()`), `market` (`aave v3`, `spark`, `compound v3`)
- `supply_apy`, `borrow_apy`: fractions (0.0378 is 3.78%), the per-second rate compounded over a year;
  `borrow_apy` is empty when the market does not lend the asset out
- `utilization`: borrowed over supplied, as the market computes it
- `supplied_usd`, `borrowed_usd`: at the market's oracle price, aave's for compound

the savings rates (susds, sdai) are not in the file: governance sets them, a change is an event, not a series.
a rerun for the same day replaces that day's rows. columns are only ever appended.
