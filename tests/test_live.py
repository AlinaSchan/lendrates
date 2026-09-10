"""talks to a public rpc. skipped unless LENDRATES_LIVE=1."""
import os

import pytest

from lendrates import abi, markets
from lendrates.abi import calldata
from lendrates.rpc import Rpc

pytestmark = pytest.mark.skipif(os.environ.get("LENDRATES_LIVE") != "1", reason="set LENDRATES_LIVE=1")


@pytest.mark.parametrize(("symbol", "comet"), markets.COMETS)
def test_each_comet_lends_the_token_the_table_says(symbol, comet):
    rpc = Rpc()
    base = abi.address(rpc.eth_call(comet, calldata("baseToken()")))
    assert abi.string(rpc.eth_call(base, calldata("symbol()"))) == symbol


def test_the_default_assets_are_aave_reserves_with_sane_rates():
    rpc = Rpc()
    block = rpc.block_number() - 1
    rows = markets.aave_like(rpc, "aave v3", markets.AAVE_LIKE[0][1], {a.lower() for a in markets.DEFAULT_ASSETS}, block)
    assert {r.asset for r in rows} == set(markets.DEFAULT_ASSETS)
    usdc = next(r for r in rows if r.asset == "USDC")
    assert 0 <= usdc.supply_apy < usdc.borrow_apy < 0.5 and 0 < usdc.utilization <= 1 and 0.95 < usdc.price < 1.05


def test_the_savings_vaults_hold_what_they_say():
    rpc = Rpc()
    for vault, symbol in ((markets.SUSDS, "USDS"), (markets.SDAI, "DAI")):
        underlying = abi.address(rpc.eth_call(vault, calldata("asset()")))
        assert abi.string(rpc.eth_call(underlying, calldata("symbol()"))) == symbol
    saved = markets.savings(rpc, rpc.block_number() - 1)
    assert [s.token for s in saved] == ["sUSDS", "sDAI"] and all(0 <= s.apy < 0.2 for s in saved)
