from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class TradeRequest:
    side: str                 # "buy" or "sell"
    fromToken: str            # token address or symbol
    toToken: str              # token address or symbol
    amount: str               # human-readable amount (e.g., "0.1")
    slippageBps: int = 100    # default 1.00%
    recipient: Optional[str] = None
    chain: str = "base"
    notes: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TradeRequest":
        if not isinstance(d, dict):
            raise ValueError("TradeRequest must be a dict")
        side = str(d.get("side", "")).lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        from_token = d.get("fromToken")
        to_token = d.get("toToken")
        amount = str(d.get("amount", "")).strip()
        if not from_token or not to_token or not amount:
            raise ValueError("fromToken, toToken, and amount are required")
        slippage_bps = int(d.get("slippageBps", 100))
        if slippage_bps < 0 or slippage_bps > 2000:
            raise ValueError("slippageBps out of range (0-2000)")
        recipient = d.get("recipient")
        chain = str(d.get("chain", "base")).lower()
        notes = d.get("notes")
        return TradeRequest(
            side=side,
            fromToken=from_token,
            toToken=to_token,
            amount=amount,
            slippageBps=slippage_bps,
            recipient=recipient,
            chain=chain,
            notes=notes,
        )

    def slippage_percent(self) -> float:
        return self.slippageBps / 100.0
