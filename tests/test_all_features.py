"""Representative feature checks execute real handlers, not simulated passes."""
import pytest
from src import server
from src.models import StockData

@pytest.mark.asyncio
async def test_basic_setup():
    tools = {tool.name for tool in await server.server.list_tools()}
    assert {'get_stock_fundamentals', 'earnings_screener', 'get_stock_news',
            'get_sector_performance', 'get_options_chain'} <= tools

def test_stock_fundamentals(monkeypatch):
    monkeypatch.setattr(server.finviz_client, 'get_multiple_stocks_fundamentals',
                        lambda *a, **kw: [{'ticker': 'NVDA', 'price': 100, 'pe_ratio': None}])
    result = server.get_stock_fundamentals('NVDA', ['price', 'pe_ratio'])
    row = result.structuredContent['stocks'][0]
    assert row['values'] == {'price': 100, 'pe_ratio': None}
    assert row['missing_fields'] == ['pe_ratio']

def test_screeners(monkeypatch):
    monkeypatch.setattr(server.finviz_screener, 'earnings_screener', lambda **kw: [
        StockData(ticker='NVDA', company_name='Nvidia', sector='Technology',
                  industry='Semiconductors', price=100)])
    text = server.earnings_screener('this_week')[0].text
    assert 'NVDA' in text and '$100.00' in text

def test_news_functions(monkeypatch):
    monkeypatch.setattr(server.finviz_news, 'get_stock_news', lambda *a, **kw: [
        {'title': 'Fixture headline', 'url': 'https://publisher.test/report'}])
    result = server.get_stock_news('NVDA')
    assert result.structuredContent['rows'] == [
        {'title': 'Fixture headline', 'url': 'https://publisher.test/report'}]

def test_performance_analysis(monkeypatch):
    def provider(*, sectors):
        assert sectors == ['Technology']
        return [{'name': 'Technology', 'performance_1d': 2.5}]
    monkeypatch.setattr(server.finviz_sector, 'get_sector_performance', provider)
    result = server.get_sector_performance(['Technology'])
    assert result.structuredContent['rows'] == [{'name': 'Technology', 'performance_1d': 2.5}]
