# changelog

all notable changes to lendrates. the format follows [keep a changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [semver](https://semver.org/) as far as a command line tool has an api.

## [0.1.0] - 2026-09-10

first cut: supply and borrow rates on aave v3, spark and compound v3, and the sky savings rates.

- aave v3 and spark from `getReserveData`, sizes from the a-token and debt-token supplies, prices from each pool's oracle
- the six compound v3 comets, their usd sizes at aave's oracle price (the weth comet prices in eth)
- susds (`ssr`) and sdai (the pot's `dsr`)
- every number read at one block, in batches; the table, `--json`, `--assets all --min-usd`, and a daily workflow
  that writes `data/daily.csv`
