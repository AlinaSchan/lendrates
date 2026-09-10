"""the markets: aave v3 and spark (the same pool code), the compound v3 comets, the sky savings rates.
every number is read from the contracts at one block; the prices come from the markets' own oracles."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import abi
from .abi import calldata

SECONDS_PER_YEAR = 365 * 24 * 3600
RAY = 10**27

AAVE_LIKE = (
    ("aave v3", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"),
    ("spark", "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"),
)
# comet address and the base token it lends out (the live test checks baseToken() against it)
COMETS = (
    ("USDC", "0xc3d688B66703497DAA19211EEdff47f25384cdc3"),
    ("USDT", "0x3Afdc9BCA9213A35503b077a6072F3D0d5AB0840"),
    ("USDS", "0x5D409e56D886231aDAf00c8775665AD0f9897b56"),
    ("WETH", "0xA17581A9E3356d9A858b789D68B4d866e593aE94"),
    ("WBTC", "0xe85Dc543813B8c2CFEaAc371517b925a166a9293"),
    ("wstETH", "0x3D0bb1ccaB520A66e607822fC55BC921738fAFE3"),
)
SUSDS = "0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"
SDAI = "0x83F20F44975D03b1b09e64809B757c47f942BEeA"

DEFAULT_ASSETS = ("USDC", "USDT", "DAI", "USDS", "USDe", "PYUSD", "WETH")


@dataclass
class Row:
    asset: str
    market: str
    supply_apy: float | None
    borrow_apy: float | None  # None when the market does not lend the asset out
    utilization: float | None
    supplied: float  # in the token
    borrowed: float
    price: float | None  # usd per token
    flags: list[str] = field(default_factory=list)

    @property
    def supplied_usd(self) -> float | None:
        return None if self.price is None else self.supplied * self.price

    @property
    def borrowed_usd(self) -> float | None:
        return None if self.price is None else self.borrowed * self.price


@dataclass
class Savings:
    name: str
    token: str
    underlying: str
    apy: float
    assets: float  # in the underlying


def apy_from_apr(apr: float) -> float:
    """an apr compounded every second, which is how aave's interface turns its rates into an apy."""
    return math.expm1(SECONDS_PER_YEAR * math.log1p(apr / SECONDS_PER_YEAR))


def apy_from_per_second(rate: float) -> float:
    """a rate per second (compound's getSupplyRate / 1e18, or a ray multiplier through ray_rate) over a year."""
    return math.expm1(SECONDS_PER_YEAR * math.log1p(rate))


def ray_rate(multiplier: int) -> float:
    """1.000000001121... in ray -> 1.121...e-9 per second, subtracted in integers: `x / RAY - 1` in floats
    would lose the digits that matter to the rounding of 1.0."""
    return (multiplier - RAY) / RAY


def reserve_config(config: int) -> tuple[int, bool, list[str]]:
    """aave v3 ReserveConfiguration bits: decimals 48-55, active 56, frozen 57, borrowing enabled 58, paused 60."""
    decimals = (config >> 48) & 0xFF
    active = bool((config >> 56) & 1)
    flags = [name for bit, name in ((57, "frozen"), (60, "paused")) if (config >> bit) & 1]
    if not (config >> 58) & 1:
        flags.append("no borrowing")
    return decimals, active, flags


def oracle_of(rpc, pool: str, block) -> tuple[str, int]:
    provider = abi.address(rpc.eth_call(pool, calldata("ADDRESSES_PROVIDER()"), block))
    oracle = abi.address(rpc.eth_call(provider, calldata("getPriceOracle()"), block))
    return oracle, abi.uint(rpc.eth_call(oracle, calldata("BASE_CURRENCY_UNIT()"), block))


def aave_like(rpc, market: str, pool: str, wanted: set[str] | None, block) -> list[Row]:
    """every reserve whose symbol is wanted (None: all of them) with its rates, sizes and oracle price."""
    oracle, unit = oracle_of(rpc, pool, block)
    assets = abi.address_array(rpc.eth_call(pool, calldata("getReservesList()"), block))
    symbols = [abi.string(b) if b else "?" for b in rpc.eth_calls([(a, calldata("symbol()")) for a in assets], block)]
    chosen = [(a, s) for a, s in zip(assets, symbols, strict=True) if wanted is None or s.lower() in wanted]
    datas = rpc.eth_calls([(pool, calldata("getReserveData(address)", abi.address_word(a))) for a, _ in chosen], block)
    reserves = []
    for (asset, symbol), data in zip(chosen, datas, strict=True):
        if not data:
            continue
        decimals, active, flags = reserve_config(abi.uint(data, 0))
        if not active:
            continue
        rates = abi.uint(data, 2), abi.uint(data, 4)  # currentLiquidityRate, currentVariableBorrowRate
        tokens = abi.address(data, 8), abi.address(data, 10)  # the a-token, the variable debt token
        reserves.append((asset, symbol, decimals, flags, *rates, *tokens))
    calls = []
    for asset, _, _, _, _, _, a_token, debt_token in reserves:
        calls += [(a_token, calldata("totalSupply()")), (debt_token, calldata("totalSupply()")),
                  (oracle, calldata("getAssetPrice(address)", abi.address_word(asset)))]
    answers = rpc.eth_calls(calls, block)
    rows = []
    for i, (_, symbol, decimals, flags, liquidity_rate, borrow_rate, _, _) in enumerate(reserves):
        supplied_raw, borrowed_raw, price_raw = (abi.uint(b) if b else 0 for b in answers[3 * i:3 * i + 3])
        rows.append(Row(symbol, market, apy_from_apr(liquidity_rate / RAY),
                        None if "no borrowing" in flags else apy_from_apr(borrow_rate / RAY),
                        borrowed_raw / supplied_raw if supplied_raw else None,
                        supplied_raw / 10**decimals, borrowed_raw / 10**decimals,
                        price_raw / unit if price_raw and unit else None, flags))
    return rows


def comets(rpc, wanted: set[str] | None, block, prices: dict[str, float]) -> list[Row]:
    """compound v3: one comet per base token. the comet prices in its own numeraire (the weth comet in
    eth), so the usd sizes use `prices` (symbol -> usd, from aave's oracle) instead."""
    picked = [(s, c) for s, c in COMETS if wanted is None or s.lower() in wanted]
    reads = ("getUtilization()", "totalSupply()", "totalBorrow()", "baseScale()")
    first = rpc.eth_calls([(c, calldata(sig)) for _, c in picked for sig in reads], block)
    live = []
    for i, (symbol, comet) in enumerate(picked):
        parts = first[4 * i:4 * i + 4]
        if any(p is None for p in parts):
            continue
        live.append((symbol, comet, *(abi.uint(p) for p in parts)))
    kinds = ("getSupplyRate(uint256)", "getBorrowRate(uint256)")
    rates = rpc.eth_calls([(c, calldata(sig, abi.word(u))) for _, c, u, *_ in live for sig in kinds], block)
    rows = []
    for i, (symbol, _, utilization, supplied, borrowed, scale) in enumerate(live):
        supply_rate, borrow_rate = (abi.uint(b) if b else 0 for b in rates[2 * i:2 * i + 2])
        rows.append(Row(symbol, "compound v3", apy_from_per_second(supply_rate / 1e18), apy_from_per_second(borrow_rate / 1e18),
                        utilization / 1e18, supplied / scale, borrowed / scale, prices.get(symbol.lower())))
    return rows


def savings(rpc, block) -> list[Savings]:
    """the sky savings rate (susds) and the dai savings rate (sdai, through the pot). both are per-second
    multipliers in ray set by governance; nothing is lent out, so there is no utilization."""
    ssr, susds_assets, pot, sdai_assets = rpc.eth_calls([
        (SUSDS, calldata("ssr()")), (SUSDS, calldata("totalAssets()")), (SDAI, calldata("pot()")), (SDAI, calldata("totalAssets()")),
    ], block)
    out = []
    if ssr and susds_assets:
        apy = apy_from_per_second(ray_rate(abi.uint(ssr)))
        out.append(Savings("sky savings rate", "sUSDS", "USDS", apy, abi.uint(susds_assets) / 1e18))
    if pot and sdai_assets:
        dsr = rpc.eth_calls([(abi.address(pot), calldata("dsr()"))], block)[0]
        if dsr:
            apy = apy_from_per_second(ray_rate(abi.uint(dsr)))
            out.append(Savings("dai savings rate", "sDAI", "DAI", apy, abi.uint(sdai_assets) / 1e18))
    return out
