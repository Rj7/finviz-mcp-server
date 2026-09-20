"""Provider failure and exact-projection regressions from candidate review."""
import asyncio
from unittest.mock import Mock
import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from src import server


def response(body):
    value = Mock()
    value.text = body
    value.content = body.encode()
    return value


@pytest.mark.parametrize('body', [
    '<!DOCTYPE html><html>Please log in</html>',
    '{"error":"Your subscription has expired"}',
    'Your subscription has expired',
    'Unrecognized,Columns\n',
    'Ticker,Price\nNVDA,"unterminated',
])
def test_registered_screener_rejects_invalid_provider_csv(monkeypatch, body):
    monkeypatch.setattr(server.finviz_screener, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_screener, '_make_request', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_|PROVIDER_CONTRACT'):
        asyncio.run(server.server.call_tool('get_relative_volume_stocks', {'min_relative_volume':2}))


def test_registered_screener_accepts_valid_empty_csv(monkeypatch):
    monkeypatch.setattr(server.finviz_screener, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_screener, '_make_request', lambda *a, **kw: response('Ticker,Price\n'))
    value = asyncio.run(server.server.call_tool('get_relative_volume_stocks', {'min_relative_volume':2}))
    assert value.structuredContent['stocks'] == []
    assert not value.isError


@pytest.mark.parametrize('body', ['{"error":"Your subscription has expired"}', 'Subscription expired', 'Unexpected,Columns\n'])
def test_registered_news_rejects_non_csv_error_bodies(monkeypatch, body):
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_|PROVIDER_CONTRACT'):
        asyncio.run(server.server.call_tool('get_market_news', {}))


def test_registered_news_accepts_valid_empty_csv(monkeypatch):
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response('Title,Date,Source,Link\n'))
    value = asyncio.run(server.server.call_tool('get_market_news', {}))
    assert value.structuredContent['rows'] == []
    assert not value.isError


@pytest.mark.parametrize('body', [
    '<html><title>Access Denied</title><body>Access Denied: log in</body></html>',
    '<html><title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title><body>Request blocked</body></html>',
    '<html><title>SEC.gov | Request Rate Threshold Exceeded</title><body>Try later</body></html>',
])
def test_registered_filing_rejects_sec_denials(monkeypatch, body):
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '1')
    monkeypatch.setattr(server.edgar_client.session, 'get', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_AUTH|RATE_LIMITED'):
        asyncio.run(server.server.call_tool('get_edgar_filing_content', {'ticker':'NVDA','accession_number':'0001-26-01','primary_document':'report.htm'}))


def test_legitimate_filing_can_discuss_errors(monkeypatch):
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '1')
    monkeypatch.setattr(server.edgar_client.session, 'get', lambda *a, **kw: response('<html><title>Annual Report</title><body>Risk factors: access denied to facilities; accounting error corrected.</body></html>'))
    value = asyncio.run(server.server.call_tool('get_edgar_filing_content', {'ticker':'NVDA','accession_number':'0001-26-01','primary_document':'report.htm'}))
    assert 'accounting error' in value.structuredContent['content']


@pytest.mark.parametrize('name,args', [
    ('get_stock_fundamentals', {'ticker':'NVDA','data_fields':['target_price']}),
    ('get_multiple_stocks_fundamentals', {'tickers':['NVDA','MSFT'],'data_fields':['target_price']}),
])
def test_missing_target_price_is_not_current_price(monkeypatch, name, args):
    monkeypatch.setattr(server.finviz_client, '_fetch_csv_from_url', lambda *a, **kw: pd.DataFrame([{'Ticker':'NVDA','Price':100,'EPS (ttm)':2},{'Ticker':'MSFT','Price':200,'EPS (ttm)':4}]))
    value = asyncio.run(server.server.call_tool(name, args))
    for row in value.structuredContent['stocks']:
        assert row['values'] == {'target_price':None}
        assert row['missing_fields'] == ['target_price']
