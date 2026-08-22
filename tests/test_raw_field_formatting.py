"""
Unit rendering for raw FinViz column values.

FinViz pre-scales several columns (market cap in millions, average volume in
thousands) and expresses the 52-week/all-time levels as the percent distance of
the current price from that level. Rendering those with the wrong unit produced
impossible output such as a negative dollar "52W High", so pin the semantics.
"""
import pytest

from src.utils.formatters import (
    format_large_number,
    format_raw_field_label,
    format_raw_field_value,
)


class TestFormatLargeNumber:
    def test_positive_suffixes(self):
        assert format_large_number(4.53e12) == "4530.00B"
        assert format_large_number(8.1965e8) == "819.65M"
        assert format_large_number(2.3e6) == "2.30M"
        assert format_large_number(950) == "950"

    def test_negatives_keep_their_suffix(self):
        # Loss-making companies have negative income; the pre-fix comparison
        # (num >= 1e9) sent every negative to the raw-digits branch.
        assert format_large_number(-3.6e8) == "-360.00M"
        assert format_large_number(-2.5e9) == "-2.50B"


class TestRawFieldLabels:
    @pytest.mark.parametrize("field,expected", [
        ("return_on_invested_capital", "Return On Invested Capital"),
        ("52_week_high", "52 Week High"),
        ("lt_debt_equity", "LT Debt Equity"),
        ("eps_next_q", "EPS Next Q"),
        ("cash_sh", "Cash/Share"),
        ("ev_sales", "EV Sales"),
        ("p_e", "P/E"),
        ("p_s", "P/S"),
    ])
    def test_labels(self, field, expected):
        assert format_raw_field_label(field) == expected


class TestRawFieldValues:
    def test_millions_columns_are_scaled(self):
        # AAPL market cap arrives as 4527844.25 (millions).
        assert format_raw_field_value("market_cap", 4527844.25) == "$4527.84B"
        # WYFI sales arrive as 0.76 (millions) => $760K, not $0.76.
        assert format_raw_field_value("sales", 0.76) == "$760.00K"
        assert format_raw_field_value("income", -0.36) == "$-360.00K"

    def test_share_count_columns_have_no_dollar_sign(self):
        assert format_raw_field_value("shares_outstanding", 14608.96) == "14.61B sh"
        assert format_raw_field_value("short_interest", 141.61) == "141.61M sh"

    def test_average_volume_is_thousands(self):
        # 56197.56 (thousands) is AAPL's ~56.2M average volume, not 56,197.
        assert format_raw_field_value("average_volume", 56197.56) == "56.20M"

    def test_52_week_levels_are_percentages_not_prices(self):
        # The regression that started this: "$-42.24" as a 52-week HIGH.
        assert format_raw_field_value("52_week_high", -55.05) == "-55.05%"
        assert format_raw_field_value("52_week_low", 100.47) == "100.47%"
        assert format_raw_field_value("all_time_high", -55.05) == "-55.05%"

    def test_currency_columns(self):
        assert format_raw_field_value("target_price", 40.89) == "$40.89"
        assert format_raw_field_value("eps_next_q", -0.33) == "$-0.33"

    def test_performance_columns_match_by_prefix(self):
        assert format_raw_field_value("performance_half_year", 20.98) == "20.98%"
        assert format_raw_field_value("performance_15_minutes", -0.5) == "-0.50%"

    def test_numeric_strings_are_coerced(self):
        # Several columns come back as strings from the CSV parser.
        assert format_raw_field_value("short_interest", "141.61") == "141.61M sh"
        assert format_raw_field_value("enterprise_value", "4549789.25") == "$4549.79B"

    def test_unknown_and_non_numeric_values_pass_through_unchanged(self):
        # A guessed unit is worse than a bare value.
        assert format_raw_field_value("52_week_range", "10.51 - 46.87") == "10.51 - 46.87"
        assert format_raw_field_value("ipo_date", "8/7/2025") == "8/7/2025"
        assert format_raw_field_value("current_ratio", 0.77) == "0.77"
        assert format_raw_field_value("sector", "Technology") == "Technology"

    def test_none_is_na(self):
        assert format_raw_field_value("sales", None) == "N/A"
