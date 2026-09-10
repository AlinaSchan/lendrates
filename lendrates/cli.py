"""command line entry point: lendrates [--assets USDC,USDT,...|all] [--markets aave,spark,compound,sky] [--json]"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from . import __version__
from .markets import AAVE_LIKE, COMETS, DEFAULT_ASSETS, Row, aave_like, comets, savings
from .rpc import Rpc, RpcError, RpcUnavailable

MARKETS = ("aave", "spark", "compound", "sky")
SUMMARY_FIELDS = ("date_utc", "block", "asset", "market", "supply_apy", "borrow_apy", "utilization", "supplied_usd", "borrowed_usd")
BUSY = 0.90  # utilization at which a large withdrawal may have to wait


def usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if value >= size:
            return f"${value / size:.2f}{suffix}" if value < 10 * size else f"${value / size:.0f}{suffix}"
    return f"${value:.0f}"


def amount(value: float) -> str:
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if value >= size:
            return f"{value / size:.2f}{suffix}"
    return f"{value:.0f}"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2%}"


def order(rows: list[Row], assets: list[str] | None) -> list[Row]:
    """by asset in the order asked for (else by size), and within an asset the best supply rate first."""
    rank = {a.lower(): i for i, a in enumerate(assets or [])}
    size: dict[str, float] = {}
    for r in rows:
        size[r.asset] = size.get(r.asset, 0) + (r.supplied_usd or 0)
    return sorted(rows, key=lambda r: (rank.get(r.asset.lower(), len(rank)), -size[r.asset], r.asset, -(r.supply_apy or 0)))


def summary_rows(date_utc: str, block: int, rows: list[Row]) -> list[dict]:
    return [{"date_utc": date_utc, "block": block, "asset": r.asset, "market": r.market,
             "supply_apy": f"{r.supply_apy:.6f}" if r.supply_apy is not None else "",
             "borrow_apy": f"{r.borrow_apy:.6f}" if r.borrow_apy is not None else "",
             "utilization": f"{r.utilization:.6f}" if r.utilization is not None else "",
             "supplied_usd": f"{r.supplied_usd:.0f}" if r.supplied_usd is not None else "",
             "borrowed_usd": f"{r.borrowed_usd:.0f}" if r.borrowed_usd is not None else ""} for r in rows]


def append_summary(path: str, rows: list[dict]) -> None:
    """one row per (date, asset, market): a rerun on the same utc day replaces that day's rows."""
    existing: list[dict] = []
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, newline="", encoding="utf-8") as f:
            existing = [r for r in csv.DictReader(f) if r.get("date_utc")]
    key = lambda r: (r["date_utc"], r["asset"], r["market"])  # noqa: E731
    fresh = {key(r) for r in rows}
    merged = sorted([r for r in existing if key(r) not in fresh] + rows, key=key)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)


def table(block: int, stamp: datetime, rows: list[Row], saved) -> str:
    lines = [f"lending on ethereum mainnet, block {block:,} ({stamp:%Y-%m-%d %H:%M} utc)", ""]
    if rows:
        lines.append(f"{'asset':<8} {'market':<12} {'supply apy':>10} {'borrow apy':>10} {'util':>7}  {'supplied':>9} {'borrowed':>9}")
        previous = None
        for r in rows:
            if previous and previous != r.asset:
                lines.append("")
            previous = r.asset
            util = "-" if r.utilization is None else f"{r.utilization:.1%}" + ("*" if r.utilization >= BUSY else " ")
            flags = f"  ({', '.join(r.flags)})" if r.flags else ""
            lines.append(f"{r.asset[:8]:<8} {r.market:<12} {pct(r.supply_apy):>10} {pct(r.borrow_apy):>10} {util:>7}  "
                         f"{usd(r.supplied_usd):>9} {usd(r.borrowed_usd):>9}{flags}")
    if saved:
        lines += ["", "savings rates, set by governance, nothing lent out:"]
        lines += [f"  {s.token:<6} {s.name:<18} {s.apy:>6.2%} on {amount(s.assets)} {s.underlying}" for s in saved]
    lines += ["", "apy: the rate compounded every second, the way aave's interface shows it; compound's shows the apr.",
              "usd at each market's oracle price (aave's for compound, whose weth market prices in eth).",
              f"* utilization at or above {BUSY:.0%}: nearly all of it is lent out, a large withdrawal may have to wait for repayments."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lendrates",
                                 description="supply and borrow rates on aave v3, spark and compound v3, and the sky savings rates.")
    ap.add_argument("--assets", default=",".join(DEFAULT_ASSETS),
                    help=f"comma separated symbols, or 'all' for every reserve above --min-usd (default {','.join(DEFAULT_ASSETS)})")
    ap.add_argument("--markets", default=",".join(MARKETS), help=f"comma separated, from: {', '.join(MARKETS)} (default: all)")
    ap.add_argument("--min-usd", type=float, default=None,
                    help="leave out rows with less supplied, in usd (default 10m with --assets all, else 0)")
    ap.add_argument("--json", action="store_true", help="json instead of the table")
    ap.add_argument("--summary-append", metavar="PATH", help="one row per asset and market appended to a csv (one set per utc date)")
    ap.add_argument("--quiet", action="store_true", help="no table on stdout")
    ap.add_argument("--rpc", action="append", metavar="URL", help="json-rpc endpoint (repeatable, tried in order)")
    ap.add_argument("--version", action="version", version=f"lendrates {__version__}")
    args = ap.parse_args(argv)

    every = args.assets.strip().lower() == "all"
    assets = None if every else [a.strip() for a in args.assets.split(",") if a.strip()]
    wanted = None if every else {a.lower() for a in assets}
    markets = [m.strip().lower() for m in args.markets.split(",") if m.strip()]
    unknown = [m for m in markets if m not in MARKETS]
    if unknown or not markets:
        print(f"error: unknown market {', '.join(unknown) or '(none)'}, pick from {', '.join(MARKETS)}", file=sys.stderr)
        return 2
    min_usd = args.min_usd if args.min_usd is not None else (10e6 if every else 0)

    rpc = Rpc(args.rpc) if args.rpc else Rpc()
    rows: list[Row] = []
    saved = []
    try:
        block = rpc.block_number() - 1
        stamp = datetime.fromtimestamp(rpc.block_time(block), tz=timezone.utc)
        for (name, pool), key in zip(AAVE_LIKE, ("aave", "spark"), strict=True):
            if key in markets:
                try:
                    rows += aave_like(rpc, name, pool, wanted, block)
                except RpcError as exc:  # a market that reverts is left out, the others still count
                    print(f"warning: {name} left out ({exc})", file=sys.stderr)
        if "compound" in markets:
            # the comets price in their own numeraire (weth in eth), so their base tokens get aave's oracle price
            needed = {s.lower() for s, _ in COMETS if wanted is None or s.lower() in wanted}
            prices = {r.asset.lower(): r.price for r in rows if r.market == "aave v3" and r.price}
            if not needed <= set(prices):
                try:
                    prices = {r.asset.lower(): r.price for r in aave_like(rpc, "aave v3", AAVE_LIKE[0][1], needed, block) if r.price}
                except RpcError:
                    prices = {}
            rows += comets(rpc, wanted, block, prices)
        if "sky" in markets and (wanted is None or wanted & {"usds", "dai", "susds", "sdai"}):
            saved = savings(rpc, block)
    except (RpcError, RpcUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rows = order([r for r in rows if (r.supplied_usd or 0) >= min_usd or (r.supplied_usd is None and min_usd == 0)], assets)
    if assets:
        present = {r.asset.lower() for r in rows} | {s.underlying.lower() for s in saved}
        missing = [a for a in assets if a.lower() not in present]
        if missing:
            print(f"note: not in these markets: {', '.join(missing)}", file=sys.stderr)
    if args.summary_append:
        append_summary(args.summary_append, summary_rows(stamp.strftime("%Y-%m-%d"), block, rows))
    if args.quiet:
        return 0
    if args.json:
        print(json.dumps({"block": block, "time_utc": stamp.isoformat(),
                          "rows": [{**r.__dict__, "supplied_usd": r.supplied_usd, "borrowed_usd": r.borrowed_usd} for r in rows],
                          "savings": [s.__dict__ for s in saved]}, indent=2))
    else:
        print(table(block, stamp, rows, saved))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
