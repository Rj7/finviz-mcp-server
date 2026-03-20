"""Finviz Elite options chain client."""

import logging
from io import StringIO
from typing import Dict, List, Optional, Any

import pandas as pd

from .base import FinvizClient

logger = logging.getLogger(__name__)

OPTIONS_EXPORT_URL = f"{FinvizClient.BASE_URL}/export/options"


class FinvizOptionsClient(FinvizClient):
    """Fetch options chain data from Finviz Elite."""

    def get_options_chain(
        self,
        ticker: str,
        option_type: str = "call",
        expiration: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch options chain for a ticker.

        Args:
            ticker: Stock ticker symbol.
            option_type: 'call' or 'put'.
            expiration: Expiration date as YYYY-MM-DD. If omitted, returns
                        the nearest expiration.

        Returns:
            List of option contract dicts with keys: contract, last, strike,
            last_close, bid, ask, change, change_pct, volume, open_interest,
            type, iv, delta, gamma, theta, vega, rho.
        """
        params: Dict[str, Any] = {
            "t": ticker.upper(),
            "ty": "oc" if option_type == "call" else "op",
        }
        if expiration:
            params["e"] = expiration
        if self.api_key:
            params["auth"] = self.api_key

        # Options endpoint doesn't use ft=4 like the screener exports,
        # so we make the request directly instead of using _fetch_csv_from_url.
        try:
            response = self._make_request(OPTIONS_EXPORT_URL, params)

            if response.text.startswith("<!DOCTYPE html>") or "<html" in response.text.lower():
                logger.error("Received HTML instead of CSV from options endpoint")
                return []
            if not response.text.strip():
                logger.error("Empty response from options endpoint")
                return []

            df = pd.read_csv(StringIO(response.text))
        except Exception as e:
            logger.error(f"Error fetching options chain: {e}")
            return []

        if df.empty:
            return []

        # Normalise column names to snake_case keys.
        # When no expiration is specified, the response includes an "Expiry"
        # column (full chain across all dates).
        col_map = {
            "Contract Name": "contract",
            "Last Trade": "last_trade_time",
            "Expiry": "expiration",
            "Strike": "strike",
            "Last Close": "last_close",
            "Bid": "bid",
            "Ask": "ask",
            "Change $": "change",
            "Change %": "change_pct",
            "Volume": "volume",
            "Open Int.": "open_interest",
            "Type": "type",
            "IV": "iv",
            "Delta": "delta",
            "Gamma": "gamma",
            "Theta": "theta",
            "Vega": "vega",
            "Rho": "rho",
        }

        results: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            contract: Dict[str, Any] = {}
            for csv_col, key in col_map.items():
                val = row.get(csv_col)
                if val is not None and str(val).strip() not in ("", "-", "nan"):
                    contract[key] = val
                else:
                    contract[key] = None
            results.append(contract)

        return results
