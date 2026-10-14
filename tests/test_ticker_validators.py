#!/usr/bin/env python3
"""
Ticker validation/parsing tests

get_stock_news advertises `tickers` as `str | list[str]`, so both forms must
validate and parse identically. A list used to fail validation outright.
"""

import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.utils.validators import validate_tickers, parse_tickers


def test_validate_tickers_string_forms():
    assert validate_tickers("NVDA") == True
    assert validate_tickers("NVDA,AMD,TSM") == True
    assert validate_tickers("nvda, amd") == True  # lowercase + spaces
    assert validate_tickers("") == False
    assert validate_tickers("NOTATICKER") == False  # over 5 characters
    assert validate_tickers("NVDA,123") == False


def test_validate_tickers_list_forms():
    assert validate_tickers(["NVDA"]) == True
    assert validate_tickers(["NVDA", "AMD", "TSM", "AVGO", "MU"]) == True
    assert validate_tickers(["nvda", "amd"]) == True
    assert validate_tickers([]) == False
    assert validate_tickers(["NVDA", "NOTATICKER"]) == False
    assert validate_tickers(["NVDA", 123]) == False  # non-string element
    assert validate_tickers(None) == False
    assert validate_tickers(42) == False


def test_parse_tickers_both_forms_agree():
    expected = ["NVDA", "AMD", "TSM"]
    assert parse_tickers("NVDA,AMD,TSM") == expected
    assert parse_tickers(["NVDA", "AMD", "TSM"]) == expected
    assert parse_tickers(" nvda , amd ,tsm ") == expected
    assert parse_tickers(["nvda", "AMD,TSM"]) == expected  # mixed nesting
    assert parse_tickers([]) == []
    assert parse_tickers(None) == []


if __name__ == "__main__":
    test_validate_tickers_string_forms()
    test_validate_tickers_list_forms()
    test_parse_tickers_both_forms_agree()
    print("✓ All ticker validator tests passed")
