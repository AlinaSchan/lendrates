"""a tiny json-rpc client over urllib: fallback across public endpoints, and batches of eth_call."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

DEFAULT_RPCS = (
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.mevblocker.io",
    "https://eth.drpc.org",
    "https://gateway.tenderly.co/public/mainnet",
    "https://eth-mainnet.public.blastapi.io",
)
USER_AGENT = "lendrates/0.1 (+https://github.com/alinaschanz/lendrates)"


class RpcError(Exception):
    """the call itself failed (revert, bad params) - the same answer would come from every node."""


class RpcUnavailable(Exception):
    """no endpoint gave a usable answer."""


class Rpc:
    def __init__(self, urls: tuple[str, ...] | list[str] | None = None, timeout: float = 20.0):
        self.urls = list(urls or DEFAULT_RPCS)
        self.timeout = timeout
        self._lock = threading.Lock()

    def _post(self, url: str, payload) -> object:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _prefer(self, url: str) -> None:
        with self._lock:
            if url in self.urls and self.urls[0] != url:
                self.urls.remove(url)
                self.urls.insert(0, url)

    def batch(self, calls: list[tuple[str, list]]) -> list:
        """several calls in one http request; an endpoint that refuses the batch or fails one call is skipped as a whole."""
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        last_problem: str | None = None
        for url in list(self.urls):
            try:
                body = self._post(url, payload if len(payload) > 1 else payload[0])
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                last_problem = f"{url}: {exc}"
                continue
            answers = body if isinstance(body, list) else [body]
            by_id = {a.get("id"): a for a in answers if isinstance(a, dict)}
            results: list = []
            problem = None
            for i in range(len(calls)):
                answer = by_id.get(i)
                if answer is None:
                    error = answers[0].get("error") if len(answers) == 1 and isinstance(answers[0], dict) else None
                    problem = str(error.get("message", error)) if isinstance(error, dict) else "missing answer in the batch"
                    break
                error = answer.get("error")
                if error:
                    message = str(error.get("message", error)) if isinstance(error, dict) else str(error)
                    if (isinstance(error, dict) and error.get("code") == 3) or "revert" in message.lower():
                        raise RpcError(message)
                    problem = message
                    break
                results.append(answer.get("result"))
            if problem:
                last_problem = f"{url}: {problem}"
                continue
            self._prefer(url)
            return results
        raise RpcUnavailable(f"no rpc endpoint gave a usable answer ({last_problem})")

    def call(self, method: str, params: list):
        return self.batch([(method, params)])[0]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_time(self, number: int) -> int:
        return int(self.call("eth_getBlockByNumber", [hex(number), False])["timestamp"], 16)

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> bytes:
        tag = hex(block) if isinstance(block, int) else block
        return bytes.fromhex((self.call("eth_call", [{"to": to, "data": data}, tag]) or "0x")[2:])

    def eth_calls(self, calls: list[tuple[str, str]], block: int | str = "latest", per_request: int = 20) -> list[bytes | None]:
        """many eth_calls at one block, `per_request` to a batch. a call that reverts comes back as None
        instead of taking its neighbours down with it."""
        tag = hex(block) if isinstance(block, int) else block
        out: list[bytes | None] = []
        for start in range(0, len(calls), per_request):
            chunk = calls[start:start + per_request]
            try:
                got = self.batch([("eth_call", [{"to": to, "data": data}, tag]) for to, data in chunk])
                out.extend(bytes.fromhex((r or "0x")[2:]) for r in got)
            except RpcError:  # one of them reverted: ask one by one
                for to, data in chunk:
                    try:
                        out.append(self.eth_call(to, data, block))
                    except RpcError:
                        out.append(None)
        return out
