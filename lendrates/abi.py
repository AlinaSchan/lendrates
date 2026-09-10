"""the few abi helpers this needs. selectors are the first four bytes of keccak256 of the
signature; they are written out here so nothing has to be hashed at runtime."""
from __future__ import annotations

SEL = {
    # aave v3 and spark (the same pool code)
    "ADDRESSES_PROVIDER()": "0542975c",
    "getPriceOracle()": "fca513a8",
    "getAssetPrice(address)": "b3596f07",
    "BASE_CURRENCY_UNIT()": "8c89b64f",
    "getReservesList()": "d1946dbc",
    "getReserveData(address)": "35ea6a75",
    # compound v3 comets
    "baseToken()": "c55dae63",
    "baseScale()": "44c1e5eb",
    "getUtilization()": "7eb71131",
    "getSupplyRate(uint256)": "d955759d",
    "getBorrowRate(uint256)": "9fa83b5a",
    "totalBorrow()": "8285ef40",
    # sky: susds and sdai
    "ssr()": "03607ceb",
    "pot()": "4ba2363a",
    "dsr()": "487bf082",
    "totalAssets()": "01e1d114",
    "asset()": "38d52e0f",
    # erc-20
    "totalSupply()": "18160ddd",
    "symbol()": "95d89b41",
}

MASK256 = (1 << 256) - 1


def word(value: int) -> str:
    return (value & MASK256).to_bytes(32, "big").hex()


def address_word(address: str) -> str:
    return address[2:].lower().rjust(64, "0")


def calldata(signature: str, *words: str) -> str:
    return "0x" + SEL[signature] + "".join(words)


def uint(data: bytes, index: int = 0) -> int:
    return int.from_bytes(data[32 * index:32 * index + 32], "big")


def address(data: bytes, index: int = 0) -> str:
    return "0x" + data[32 * index + 12:32 * index + 32].hex()


def address_array(data: bytes) -> list[str]:
    """a dynamic address[] returned on its own (offset, length, items)."""
    offset = uint(data, 0)
    length = int.from_bytes(data[offset:offset + 32], "big")
    body = data[offset + 32:]
    return ["0x" + body[32 * i + 12:32 * i + 32].hex() for i in range(length)]


def string(data: bytes) -> str:
    """abi string, with the bytes32 fallback a few old tokens use for symbol()."""
    if len(data) == 32:
        return data.rstrip(b"\x00").decode("utf-8", errors="replace")
    offset = uint(data, 0)
    length = int.from_bytes(data[offset:offset + 32], "big")
    return data[offset + 32:offset + 32 + length].decode("utf-8", errors="replace")
