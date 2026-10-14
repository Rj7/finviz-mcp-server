"""Market overview distinguishes missing components from zero activity."""
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from src import server

def unavailable(*args, **kwargs):
    raise RuntimeError('fixture outage')

def test_market_overview_preserves_values_without_inventing_quote_time(monkeypatch):
    monkeypatch.setattr(server.finviz_client, 'get_multiple_stocks_fundamentals',
                        lambda *a, **kw: [{'ticker': 'SPY', 'price': 500}])
    for name in ('volume_surge_screener', 'uptrend_screener', 'earnings_screener'):
        monkeypatch.setattr(server.finviz_screener, name, lambda *a, **kw: [])
    result = server.get_market_overview().structuredContent
    assert result['etfs'] == [{'ticker': 'SPY', 'price': 500}]
    assert result['statistics'] == {'volume_surge_count': 0, 'uptrend_count': 0, 'earnings_this_week_count': 0}
    assert result['as_of'] is None
    assert result['partial'] is False
    assert result['errors'] == {}

def test_market_overview_partial_failure_is_not_zero_activity(monkeypatch):
    monkeypatch.setattr(server.finviz_client, 'get_multiple_stocks_fundamentals', lambda *a, **kw: [{'ticker': 'SPY', 'price': 500}])
    monkeypatch.setattr(server.finviz_screener, 'volume_surge_screener', unavailable)
    monkeypatch.setattr(server.finviz_screener, 'uptrend_screener', lambda: [])
    monkeypatch.setattr(server.finviz_screener, 'earnings_screener', lambda **kw: [])
    result = server.get_market_overview().structuredContent
    assert result['partial'] is True
    assert result['statistics']['volume_surge_count'] is None
    assert result['statistics']['uptrend_count'] == 0
    assert 'volume_surge_count' in result['errors']

def test_market_overview_all_failed_is_error(monkeypatch):
    monkeypatch.setattr(server.finviz_client, 'get_multiple_stocks_fundamentals', unavailable)
    for name in ('volume_surge_screener', 'uptrend_screener', 'earnings_screener'):
        monkeypatch.setattr(server.finviz_screener, name, unavailable)
    with pytest.raises(ToolError, match='UPSTREAM_UNAVAILABLE'):
        server.get_market_overview()
