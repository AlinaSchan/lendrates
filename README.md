# lendrates

[![ci](https://github.com/alinaschanz/lendrates/actions/workflows/ci.yml/badge.svg)](https://github.com/alinaschanz/lendrates/actions/workflows/ci.yml)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)
![license mit](https://img.shields.io/badge/license-MIT-2b7a74)
[![release](https://img.shields.io/github/v/release/alinaschanz/lendrates?color=2b7a74)](https://github.com/alinaschanz/lendrates/releases)
[![openssf scorecard](https://api.scorecard.dev/projects/github.com/alinaschanz/lendrates/badge)](https://scorecard.dev/viewer/?uri=github.com/alinaschanz/lendrates)

what a dollar earns and what borrowing one costs on ethereum's big lending markets, right now:
aave v3, spark, the compound v3 comets and the sky savings rate, read from the contracts at one
block over a public rpc. no keys, no api, no dashboard in between.

```
$ lendrates
lending on ethereum mainnet, block 25,947,470 (2026-09-10 14:04 utc)

asset    market       supply apy borrow apy    util   supplied  borrowed
USDC     compound v3       5.77%      6.90%  90.7%*      $375m     $340m
USDC     aave v3           3.79%      4.49%  93.9%*    $2.31bn   $2.17bn
USDC     spark             3.61%      4.36%  92.2%*       $26m      $24m

USDT     aave v3           3.92%      4.65%  94.1%*    $2.96bn   $2.78bn
USDT     spark             3.45%      4.08%  94.2%*      $334m     $315m
USDT     compound v3       3.16%      3.98%  86.5%       $180m     $156m

DAI      aave v3           3.13%      4.84%  87.0%       $131m     $114m
DAI      spark             2.49%      4.13%  67.7%       $308m     $208m

USDS     compound v3       3.11%      3.94%  85.1%      $1.89m    $1.61m
USDS     spark             2.35%      4.00%  65.6%       $936m     $614m
USDS     aave v3           0.27%      5.71%   6.5%      $9.40m     $613k

USDe     aave v3           1.05%      6.45%  22.2%       $668m     $148m

PYUSD    aave v3           3.98%      5.04%  88.2%      $7.58m    $6.69m
PYUSD    spark             0.62%      3.98%  17.5%       $100m      $17m

WETH     spark             1.48%      1.96%  79.8%     $1.32bn   $1.06bn
WETH     aave v3           1.46%      2.07%  83.4%     $5.15bn   $4.30bn
WETH     compound v3       1.30%      1.88%  64.8%       $123m      $80m

savings rates, set by governance, nothing lent out:
  sUSDS  sky savings rate    3.60% on 4.66bn USDS
  sDAI   dai savings rate    1.25% on 165.53m DAI

apy: the rate compounded every second, the way aave's interface shows it; compound's shows the apr.
usd at each market's oracle price (aave's for compound, whose weth market prices in eth).
* utilization at or above 90%: nearly all of it is lent out, a large withdrawal may have to wait for repayments.
```

usdc pays 5.77% on compound and 3.79% on aave, and aave is the more lent out of the two: the rate
curves differ from market to market, so utilization alone does not say who pays more. usds on aave
is the other end of it: 0.27% to supply, 5.71% to borrow, 6.5% used. and none of this includes
rewards paid in other tokens; those change weekly and are not in the contracts read here.

## install

```
pipx install git+https://github.com/alinaschanz/lendrates
```

or clone it and run `python -m lendrates` from the folder. python 3.10 or newer, no dependencies.

## use

```
lendrates                                   # usdc, usdt, dai, usds, usde, pyusd, weth everywhere
lendrates --assets USDC,USDT                # just these
lendrates --assets all                      # every reserve with at least $10m supplied
lendrates --assets all --min-usd 1e9        # the big ones only
lendrates --markets aave,spark              # leave compound and sky out
lendrates --json
lendrates --summary-append data/daily.csv --quiet   # one row per asset and market, the dataset
```

## how it works

- **aave v3 and spark** run the same pool code. `getReservesList()` and every token's `symbol()`
  pick the reserves; `getReserveData(asset)` gives `currentLiquidityRate` and
  `currentVariableBorrowRate`, both aprs in ray (1e27), and the addresses of the a-token and the
  variable debt token, whose `totalSupply()` are what is supplied and what is borrowed. decimals,
  "frozen", "paused" and "borrowing enabled" are bits of the reserve's configuration word (48-55, 57,
  60, 58). prices come from the pool's own oracle (`ADDRESSES_PROVIDER().getPriceOracle()`, usd with
  8 decimals). pool addresses: [aave v3](https://etherscan.io/address/0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2),
  [spark](https://etherscan.io/address/0xC13e21B648A5Ee794902342038FF3aDAB66BE987).
- **compound v3** is one contract per lent token (a comet): usdc, usdt, usds, weth, wbtc, wsteth.
  `getUtilization()`, then `getSupplyRate(u)` and `getBorrowRate(u)`, which are rates per second
  scaled by 1e18; `totalSupply()`, `totalBorrow()`, `baseScale()` for the sizes. a comet prices
  everything in its own numeraire (the weth comet in eth), so the usd columns use aave's oracle price
  for the same token. the live test checks every comet's `baseToken()` against the table.
- **sky**: [susds](https://etherscan.io/address/0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD)
  `ssr()` and the pot's `dsr()` behind [sdai](https://etherscan.io/address/0x83F20F44975D03b1b09e64809B757c47f942BEeA)
  are per-second multipliers in ray set by governance; `totalAssets()` is what sits in them.
- **apy**: an apr compounded every second, `(1 + apr / 31,536,000) ** 31,536,000 - 1`, and a per-second
  rate `(1 + r) ** 31,536,000 - 1`. that is how aave's interface shows it; compound's shows the apr,
  which is a little lower.
- every call is an `eth_call` at one block, twenty to a batch request; endpoints publicnode,
  mevblocker, drpc, tenderly, blastapi in that order, or `--rpc` yours. a market that reverts is left
  out with a warning, the rest still print.

## reading the table

- an apy here is the rate at this block. it moves with every deposit, borrow and repayment, and on
  a 90%+ market it can move a lot within an hour.
- `*` is not a warning about the protocol: it says the pool is nearly lent out, which is exactly when
  suppliers earn the most and when a large withdrawal can have to wait for someone to repay.
- the savings rates are set by vote and change rarely; they are not market rates.
- numbers, not calls: none of this says where money should go. rewards, risks, caps, collateral
  rules and the chance of a bad debt event are all outside these columns.

## the dataset

`data/daily.csv` gets one row per asset and market every night at 00:33 utc. columns are in
[data/README.md](data/README.md); a rerun for the same day replaces the day.

## exit codes and scripting

`0` after a table, `2` when a market name is wrong or no rpc answered. `--json` carries every row with
the raw sizes in tokens and the price used, so `lendrates --json | jq '.rows[] | select(.utilization > 0.95)'`
lists the pools that are nearly lent out.

## see also

- [stablepeg](https://github.com/alinaschanz/stablepeg): whether the dollars in these pools are still a dollar
- [bigmoves](https://github.com/alinaschanz/bigmoves), [netflows](https://github.com/alinaschanz/netflows): where large amounts of them go
- the notes: [alinaschanz.life](https://alinaschanz.life), the short version on [x](https://x.com/alinaschanz)

## verify a release

every release carries the sdist and the wheel, a `SHA256SUMS` file, an opentimestamps proof of that
file, and a build provenance attestation made in github's own signing flow. with the files downloaded
into one folder:

    sha256sum -c SHA256SUMS
    gh attestation verify ./*.whl --owner alinaschanz
    ots verify SHA256SUMS.ots

the commit itself is [signed](https://alinaschanz.life/verify/#commits).

## license

[mit](LICENSE). numbers, not calls. not financial advice.
