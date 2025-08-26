import os
from typing import Dict, Optional

import requests


class TradeServiceClient:
    """Thin client for defai-trade-service quote and agent endpoints.

    Expects the following env vars:
      - TRADE_SERVICE_BASE_URL (e.g., http://localhost:3000)
      - TRADE_SERVICE_API_KEY   (maps to SERVER_API_KEY for server auth)
    """

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or os.getenv("TRADE_SERVICE_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("TRADE_SERVICE_API_KEY")
        if not self.base_url:
            raise ValueError("TRADE_SERVICE_BASE_URL is not set")
        if not self.api_key:
            raise ValueError("TRADE_SERVICE_API_KEY is not set")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: Dict[str, str]):
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("success"):
            raise RuntimeError(f"Trade service error: {data}")
        return data

    def quote_eth_to_token(self, amount_eth: str, token_out: str, recipient: Optional[str], slippage_pct: float):
        return self._get(
            "/api/quote/eth-to-token",
            {
                "amountIn": amount_eth,
                "tokenOut": token_out,
                **({"recipientAddress": recipient} if recipient else {}),
                "slippage": str(slippage_pct),
            },
        )

    def quote_token_to_eth(self, amount_in: str, token_in: str, recipient: Optional[str], slippage_pct: float):
        return self._get(
            "/api/quote/token-to-eth",
            {
                "amountIn": amount_in,
                "tokenIn": token_in,
                **({"recipientAddress": recipient} if recipient else {}),
                "slippage": str(slippage_pct),
            },
        )

    def quote_token_to_token(self, amount_in: str, token_in: str, token_out: str, recipient: Optional[str], slippage_pct: float):
        return self._get(
            "/api/quote/token-to-token",
            {
                "amountIn": amount_in,
                "tokenIn": token_in,
                "tokenOut": token_out,
                **({"recipientAddress": recipient} if recipient else {}),
                "slippage": str(slippage_pct),
            },
        )
