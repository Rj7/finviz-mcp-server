"""Finviz Elite options chain client."""

import logging
import re
from io import StringIO
from typing import Dict, List, Optional, Any

import pandas as pd

from .base import FinvizClient
from ..agent_contracts import provider_error

logger = logging.getLogger(__name__)

OPTIONS_EXPORT_URL = f"{FinvizClient.BASE_URL}/export/options"

OPTION_TYPE_PARAMS = {"call": "oc", "put": "op"}


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
                        all expirations (can be thousands of contracts).

        Returns:
            List of option contract dicts.
        """
        params: Dict[str, Any] = {
            "t": ticker.upper(),
            "ty": OPTION_TYPE_PARAMS[option_type],
        }
        if expiration:
            params["e"] = expiration
        if self.api_key:
            params["auth"] = self.api_key

        # Options endpoint doesn't use ft=4 like the screener exports,
        # so we make the request directly instead of using _fetch_csv_from_url.
        try:
            response = self._make_request(OPTIONS_EXPORT_URL, params, retries=1)

            if response.text.startswith("<!DOCTYPE html>") or "<html" in response.text.lower():
                logger.error("Received HTML instead of CSV from options endpoint")
                raise ValueError('UPSTREAM_AUTH: options endpoint returned HTML instead of CSV')
            if not response.text.strip():
                logger.error("Empty response from options endpoint")
                raise ValueError('UPSTREAM_UNAVAILABLE: empty options response')

            if len(response.text.encode('utf-8')) > 8 * 1024 * 1024:
                raise ValueError('PROVIDER_CONTRACT: options response exceeds 8 MiB; specify expiration')

            df = pd.read_csv(StringIO(response.text))
        except Exception as e:
            raise provider_error(e) from e

        if 'Strike' not in df.columns or not ({'Type', 'Contract Name'} & set(df.columns)):
            raise ValueError('PROVIDER_CONTRACT: options CSV lacks contract side/identity columns')

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

        # Rename columns present in the DataFrame, drop unmapped ones
        rename = {csv: key for csv, key in col_map.items() if csv in df.columns}
        df = df.rename(columns=rename)[list(rename.values())]

        # Convert to records and replace NaN with None
        records = df.to_dict("records")
        for rec in records:
            for k, v in rec.items():
                if pd.isna(v):
                    rec[k] = None
            declared = str(rec.get('type') or '').strip().lower()
            side = {'c':'call','p':'put','calls':'call','puts':'put','call':'call','put':'put'}.get(declared)
            identity = re.search(r'(\d{6})([CP])\d{8}$', str(rec.get('contract') or ''))
            if identity:
                identified = 'call' if identity.group(2) == 'C' else 'put'
                if side and side != identified:
                    raise ValueError('PROVIDER_CONTRACT: conflicting contract side and identifier')
                side = identified
                rec['expiration'] = rec.get('expiration') or f'20{identity.group(1)[:2]}-{identity.group(1)[2:4]}-{identity.group(1)[4:]}'
            if not side:
                raise ValueError('PROVIDER_CONTRACT: cannot establish option side; refusing a mixed or mislabeled chain')
            rec['type'] = side
            rec['expiration'] = rec.get('expiration') or expiration
            rec['as_of'] = rec.get('last_trade_time')
        return [r for r in records if r['type'] == option_type and (not expiration or r['expiration'] == expiration)]
