# custom_screener Tool Design

## Overview

Bring the "arbitrary filter code" capability from the finviz-screener skill
into the MCP server. This adds a single generic tool that lets users pass raw
FinViz filter codes such as `cap_small,fa_div_o3,fa_pe_u20` and retrieve
screening results (CSV data) directly.

## Changed Files

### 1. `src/finviz_client/base.py` — `screen_stocks_raw()` method

`screen_stocks()` builds its URL via the internal filter dict and
`_convert_filters_to_finviz()`. The custom screener instead passes the **raw
filter string straight into the `f=` parameter**.

New method `screen_stocks_raw(filters, ...)`:

| Parameter | Description |
|---|---|
| `filters` | Pre-validated filter string (e.g. `cap_small,fa_div_o3`) |
| `signal` | Optional signal code (e.g. `ta_topgainers`) |
| `order` | Optional sort order (e.g. `-marketcap`) |
| `max_results` | Maximum results (1–500, default `None` = unlimited) |

- Uses the Elite export URL with auth parameter.
- Reuses `_fetch_csv_from_url()` and `_parse_stock_data_from_csv()`.
- Applies a **double limit** (`ar` server-side + `df.head()` client-side) to
  guard against the `ar` parameter being ignored.

### 2. `src/utils/validators.py` — Raw filter validation functions

Ported from the finviz-screener skill's regex patterns:

| Function | Pattern | Purpose |
|---|---|---|
| `validate_and_normalize_raw_filters(raw_filters)` | `^[a-z0-9_.\-\|]+$` per token | Validate & normalize comma-separated filter tokens (URL injection prevention). Returns `(errors, normalized_string)`. Max 30 tokens. |
| `validate_signal(signal)` | `^[a-z0-9_]+$` | Validate signal identifier |
| `validate_raw_sort_order(order)` | `^-?[a-z0-9_]+$` | Validate sort order (optional leading `-` for descending) |

### 3. `src/server.py` — `custom_screener` tool

Registered via `@server.tool()`:

```python
@server.tool()
def custom_screener(
    filters: str,
    signal: Optional[str] = None,
    order: Optional[str] = None,
    max_results: int = 50,
) -> List[TextContent]:
```

**Parameters:**

- `filters` (required): Comma-separated FinViz filter codes
  (e.g. `"cap_small,fa_div_o3,fa_pe_u20"`)
- `signal` (optional): Signal filter (e.g. `"ta_topgainers"`)
- `order` (optional): Sort order (e.g. `"-marketcap"`, `"change"`)
- `max_results` (optional): Max results, 1–500 (default 50). Returns an
  explicit error for out-of-range values.

**Processing flow:**

1. Validate and normalize filter string via
   `validate_and_normalize_raw_filters()`.
2. Validate optional signal / order.
3. Validate `max_results` (explicit error if out of range).
4. Call `finviz_client.screen_stocks_raw()` with the normalized string.
5. Format results using correct `StockData` attribute names
   (`company_name`, `price_change`, `pe_ratio`, etc.).
6. Include conditional `dividend_yield` and `eps_surprise` fields.

**Placement:** End of file, after `get_moving_average_position`.

### 4. `tests/test_mcp_integration.py` — Expected tools list update

Added `"custom_screener"` to the `expected_tools` list.

### 5. `tests/test_custom_screener.py` — New test file (39 tests)

| Test Class | Count | Coverage |
|---|---|---|
| `TestValidateAndNormalizeRawFilters` | 16 | Normal inputs, whitespace normalization, injection rejection, empty/None inputs, token limits |
| `TestValidateRawSortOrder` | 7 | Valid ascending/descending, invalid characters, empty/None |
| `TestValidateSignal` | 6 | Valid signals, invalid characters, empty/None |
| `TestScreenStocksRaw` | 5 | `max_results` clamping (500 cap, 0→1), `df.head()` application, signal/order parameter forwarding |
| `TestCustomScreenerTool` | 5 | Output value correctness, zero-value handling, empty results, `max_results` error, filter validation error |

## What Was Not Changed

- All 20+ preset screener tools remain untouched.
- `_convert_filters_to_finviz()` hardcoded logic is preserved as-is.
- The finviz-screener skill's "open in browser" feature is not included
  (MCP server returns data only).
- No natural-language mapping table — the LLM infers usage from the
  docstring examples.
