"""offline: the rate maths, the reserve bits, abi decoding, every market against a fake node, the table and the daily file."""
import csv
import json
import math

from lendrates import abi, cli, markets
from lendrates.markets import RAY, SECONDS_PER_YEAR, apy_from_apr, apy_from_per_second, ray_rate, reserve_config
from lendrates.rpc import RpcError

AAVE_POOL = markets.AAVE_LIKE[0][1].lower()
PROVIDER, ORACLE, POT = "0x" + "a1" * 20, "0x" + "a2" * 20, "0x" + "d0" * 20
USDC, WETH, JUNK = "0x" + "c0" * 20, "0x" + "e0" * 20, "0x" + "f0" * 20
A_USDC, D_USDC, A_WETH, D_WETH = ("0x" + h * 20 for h in ("b1", "b2", "b3", "b4"))
COMET_USDC = markets.COMETS[0][1].lower()
SUSDS, SDAI = markets.SUSDS.lower(), markets.SDAI.lower()
DSR_125 = round(math.exp(math.log(1.0125) / SECONDS_PER_YEAR) * RAY)


def w(value):
    return abi.word(value)


def aw(address):
    return abi.address_word(address)


def sym(text):
    return w(0x20) + w(len(text)) + text.encode().hex().ljust(64, "0")


def config(decimals, borrowing=True, frozen=False, active=True, paused=False):
    return decimals << 48 | int(active) << 56 | int(frozen) << 57 | int(borrowing) << 58 | int(paused) << 60


def reserve(cfg, liquidity_apr, borrow_apr, a_token, debt_token):
    words = [w(x) for x in (cfg, RAY, int(liquidity_apr * RAY), RAY, int(borrow_apr * RAY), 0, 1_789_000_000, 3, 0, 0, 0, 0, 0, 0, 0)]
    words[8], words[10] = aw(a_token), aw(debt_token)
    return "".join(words)


def per_second(apr):
    return int(apr / SECONDS_PER_YEAR * 1e18)


class FakeRpc:
    """aave (spark reverts), the usdc comet (the others revert), susds and sdai."""

    def __init__(self):
        self.answers = {
            (AAVE_POOL, "ADDRESSES_PROVIDER()"): aw(PROVIDER), (PROVIDER, "getPriceOracle()"): aw(ORACLE),
            (ORACLE, "BASE_CURRENCY_UNIT()"): w(10**8),
            (AAVE_POOL, "getReservesList()"): w(0x20) + w(3) + aw(USDC) + aw(WETH) + aw(JUNK),
            (USDC, "symbol()"): sym("USDC"), (WETH, "symbol()"): sym("WETH"), (JUNK, "symbol()"): sym("JUNK"),
            (A_USDC, "totalSupply()"): w(2_000_000_000 * 10**6), (D_USDC, "totalSupply()"): w(1_900_000_000 * 10**6),
            (A_WETH, "totalSupply()"): w(2_000_000 * 10**18), (D_WETH, "totalSupply()"): w(1_600_000 * 10**18),
            (COMET_USDC, "getUtilization()"): w(int(0.907e18)), (COMET_USDC, "totalSupply()"): w(375_000_000 * 10**6),
            (COMET_USDC, "totalBorrow()"): w(340_000_000 * 10**6), (COMET_USDC, "baseScale()"): w(10**6),
            (SUSDS, "ssr()"): w(1_000_000_001_121_484_774_769_253_326), (SUSDS, "totalAssets()"): w(4_659_000_000 * 10**18),
            (SDAI, "pot()"): aw(POT), (SDAI, "totalAssets()"): w(165_500_000 * 10**18), (POT, "dsr()"): w(DSR_125),
        }
        self.reserves = {USDC: reserve(config(6), 0.0378, 0.0449, A_USDC, D_USDC),
                         WETH: reserve(config(18, frozen=True), 0.0146, 0.0207, A_WETH, D_WETH),
                         JUNK: reserve(config(18, active=False), 0, 0, JUNK, JUNK)}
        self.prices = {USDC: 99_990_000, WETH: 242_237_000_000}
        self.rates = {"getSupplyRate(uint256)": per_second(0.0561), "getBorrowRate(uint256)": per_second(0.0667)}

    def eth_call(self, to, data, block="latest"):
        to = to.lower()
        name = next(k for k, v in abi.SEL.items() if v == data[2:10])
        if name == "getReserveData(address)" and to == AAVE_POOL:
            return bytes.fromhex(self.reserves["0x" + data[-40:]])
        if name == "getAssetPrice(address)" and to == ORACLE:
            return bytes.fromhex(w(self.prices.get("0x" + data[-40:], 0)))
        if name in self.rates and to == COMET_USDC:
            assert int(data[10:], 16) == int(0.907e18)  # the rate at the current utilization
            return bytes.fromhex(w(self.rates[name]))
        if (to, name) in self.answers:
            return bytes.fromhex(self.answers[(to, name)])
        raise RpcError("execution reverted")

    def eth_calls(self, calls, block="latest", per_request=20):
        out = []
        for to, data in calls:
            try:
                out.append(self.eth_call(to, data, block))
            except RpcError:
                out.append(None)
        return out

    def block_number(self):
        return 25_950_001

    def block_time(self, number):
        return 1_789_000_000


def test_apy_maths():
    assert abs(apy_from_apr(0.05) - math.expm1(0.05)) < 1e-6  # every second is as good as continuous
    assert abs(apy_from_per_second(ray_rate(1_000_000_001_121_484_774_769_253_326)) - 0.036) < 1e-4  # the ssr of 3.60%
    assert abs(apy_from_per_second(ray_rate(DSR_125)) - 0.0125) < 1e-6  # DSR_125 itself went through a float
    assert ray_rate(RAY) == 0 and apy_from_apr(0) == 0
    assert ray_rate(RAY + 1) == 1e-27  # a float subtraction from 1.0 would give 0 here


def test_reserve_config_bits():
    assert reserve_config(config(6)) == (6, True, [])
    assert reserve_config(config(18, borrowing=False, frozen=True)) == (18, True, ["frozen", "no borrowing"])
    assert reserve_config(config(8, paused=True))[2] == ["paused"]
    assert reserve_config(config(18, active=False))[1] is False


def test_abi_decoding():
    assert abi.address_array(bytes.fromhex(w(0x20) + w(2) + aw(USDC) + aw(WETH))) == [USDC, WETH]
    assert abi.string(bytes.fromhex(sym("USDe"))) == "USDe"
    assert abi.string(b"MKR".ljust(32, b"\x00")) == "MKR"
    assert abi.calldata("getReserveData(address)", aw(USDC)) == "0x35ea6a75" + aw(USDC)


def test_aave_like_rows():
    rows = markets.aave_like(FakeRpc(), "aave v3", AAVE_POOL, None, 1)
    assert [r.asset for r in rows] == ["USDC", "WETH"]  # the inactive reserve is left out
    usdc, weth = rows
    assert abs(usdc.supply_apy - apy_from_apr(0.0378)) < 1e-9 and abs(usdc.borrow_apy - apy_from_apr(0.0449)) < 1e-9
    assert usdc.utilization == 0.95 and usdc.supplied == 2e9 and abs(usdc.supplied_usd - 2e9 * 0.9999) < 1
    assert weth.flags == ["frozen"] and abs(weth.borrowed_usd - 1_600_000 * 2422.37) < 1
    assert [r.asset for r in markets.aave_like(FakeRpc(), "aave v3", AAVE_POOL, {"weth"}, 1)] == ["WETH"]


def test_comets_take_their_usd_price_from_outside():
    rows = markets.comets(FakeRpc(), {"usdc"}, 1, {"usdc": 1.0})
    assert len(rows) == 1 and rows[0].market == "compound v3" and rows[0].asset == "USDC"
    assert abs(rows[0].supply_apy - apy_from_per_second(per_second(0.0561) / 1e18)) < 1e-12
    assert abs(rows[0].utilization - 0.907) < 1e-12 and rows[0].supplied_usd == 375e6
    everything = markets.comets(FakeRpc(), None, 1, {})
    assert [r.asset for r in everything] == ["USDC"] and everything[0].supplied_usd is None  # the others reverted


def test_savings():
    saved = markets.savings(FakeRpc(), 1)
    assert [s.token for s in saved] == ["sUSDS", "sDAI"]
    assert abs(saved[0].apy - 0.036) < 1e-4 and abs(saved[1].apy - 0.0125) < 1e-6 and saved[0].assets == 4.659e9


def test_cli_table(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Rpc", lambda urls=None: FakeRpc())
    assert cli.main(["--assets", "USDC,WETH,USDS,DOGE"]) == 0  # usds is only a savings rate in the fake
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    usdc = [line for line in lines if line.startswith("USDC")]
    assert usdc[0].split()[1:3] == ["compound", "v3"] and usdc[1].split()[1:3] == ["aave", "v3"]  # the better supply rate first
    assert "95.0%*" in usdc[1] and "$2.00bn" in usdc[1] and "$375m" in usdc[0]
    assert any(line.startswith("WETH") and "(frozen)" in line for line in lines)
    assert "sUSDS  sky savings rate    3.60% on 4.66bn USDS" in captured.out
    assert "not in these markets: DOGE" in captured.err and "spark left out" in captured.err


def test_cli_json_and_the_daily_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "Rpc", lambda urls=None: FakeRpc())
    path = tmp_path / "daily.csv"
    assert cli.main(["--summary-append", str(path), "--quiet"]) == 0
    assert cli.main(["--summary-append", str(path), "--quiet"]) == 0  # the same day again: replaced, not doubled
    with open(path, encoding="utf-8") as f:
        daily = list(csv.DictReader(f))
    assert [(r["asset"], r["market"]) for r in daily] == [("USDC", "aave v3"), ("USDC", "compound v3"), ("WETH", "aave v3")]
    assert daily[0]["utilization"] == "0.950000" and daily[1]["supplied_usd"] == "374962500"  # 375m at aave's usdc price
    assert cli.main(["--json", "--markets", "aave,sky"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["block"] == 25_950_000 and [r["market"] for r in doc["rows"]] == ["aave v3", "aave v3"] and len(doc["savings"]) == 2


def test_cli_all_and_min_usd(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Rpc", lambda urls=None: FakeRpc())
    assert cli.main(["--assets", "all", "--markets", "aave", "--min-usd", "3e9"]) == 0
    out = capsys.readouterr().out
    assert "WETH" in out and "USDC " not in out  # 4.8bn of weth passes, 2bn of usdc does not
    assert cli.main(["--markets", "nope"]) == 2


def test_formatting():
    assert cli.usd(2.31e9) == "$2.31bn" and cli.usd(375e6) == "$375m" and cli.usd(9.8e6) == "$9.80m" and cli.usd(None) == "n/a"
    assert cli.amount(4.659e9) == "4.66bn" and cli.pct(None) == "-" and cli.pct(0.0378) == "3.78%"
