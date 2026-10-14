"""Basic local contracts; assertions propagate to pytest."""
from src.models import StockData
from src.utils.validators import validate_ticker, validate_market_cap, validate_price_range
from src.utils.formatters import format_large_number

def test_validators():
    for ticker in ('AAPL', 'MSFT', 'nvda'):
        assert validate_ticker(ticker)
    for ticker in ('TOOLONG', '', '123'):
        assert not validate_ticker(ticker)
    assert validate_market_cap('large')
    assert not validate_market_cap('invalid')
    assert validate_price_range(10, 100)
    assert not validate_price_range(100, 10)
    assert not validate_price_range(-10, 100)

def test_formatters():
    for value, expected in [(1500, '1.50K'), (1500000, '1.50M'), (1500000000, '1.50B')]:
        assert format_large_number(value) == expected

def test_data_models():
    stock = StockData(ticker='AAPL', company_name='Apple Inc.', sector='Technology',
                      industry='Consumer Electronics', price=150, volume=1000000)
    values = stock.to_dict()
    assert values['ticker'] == 'AAPL'
    assert values['price'] == 150
    assert values['volume'] == 1000000
