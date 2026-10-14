"""Failures must survive the real MCP boundary, not masquerade as empty data."""
import requests
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from src import server
from src.utils.validators import validate_price_range


@pytest.mark.parametrize('value', ['invalid', '', 'oops', float('nan'), float('inf'), True, {}])
def test_price_bound_cannot_silently_become_unset(value):
    assert validate_price_range(value, None) is False
    assert validate_price_range(None, value) is False


@pytest.mark.parametrize('lower,upper', [(None, None), (0, 0), (5, 10), ('5', '10'), ('o5', 'u10')])
def test_valid_price_bounds_remain_supported(lower, upper):
    assert validate_price_range(lower, upper) is True


def failing_request(*args, **kwargs):
    raise requests.Timeout('https://provider.test/?auth=fixture-secret')


@pytest.mark.parametrize('tool,args', [
    ('get_sec_filing_summary', {'ticker': 'NVDA'}),
    ('get_edgar_company_filings', {'ticker': 'NVDA'}),
    ('get_edgar_company_facts', {'ticker': 'NVDA'}),
    ('get_edgar_company_concept', {'ticker': 'NVDA', 'concept': 'Assets'}),
])
@pytest.mark.asyncio
async def test_provider_timeout_is_mcp_error_without_credentials(monkeypatch, tool, args):
    monkeypatch.setattr(server.finviz_sec, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_sec, '_make_request', failing_request)
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '0000000001')
    monkeypatch.setattr(server.edgar_client.client, 'get_submissions', failing_request)
    monkeypatch.setattr(server.edgar_client.client, 'get_company_facts', failing_request)
    monkeypatch.setattr(server.edgar_client.client, 'get_company_concept', failing_request)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool(tool, args)
    assert result.isError is True
    text = '\n'.join(b.text for b in result.content if b.type == 'text')
    assert 'UPSTREAM_TIMEOUT' in text
    assert 'fixture-secret' not in text


@pytest.mark.asyncio
async def test_cik_lookup_outage_is_not_no_filings(monkeypatch):
    monkeypatch.setattr(server.edgar_client.session, 'get', failing_request)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool('get_edgar_company_filings', {'ticker': 'NVDA'})
    assert result.isError is True
    assert 'UPSTREAM_TIMEOUT' in str(result.content)
    assert 'fixture-secret' not in str(result.content)


@pytest.mark.parametrize('tool,args,provider,payload', [
    ('get_sec_filing_summary', {'ticker': 'NVDA'}, 'summary', {'error': 'fixture failure'}),
    ('get_edgar_company_concept', {'ticker': 'NVDA', 'concept': 'Assets'}, 'concept', {'error': 'fixture failure'}),
])
@pytest.mark.asyncio
async def test_legacy_error_envelopes_are_not_success(monkeypatch, tool, args, provider, payload):
    owner, method = (server.finviz_sec, 'get_filing_summary') if provider == 'summary' else (server.edgar_client, 'get_company_concept')
    monkeypatch.setattr(owner, method, lambda *a, **kw: payload)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool(tool, args)
    assert result.isError is True


@pytest.mark.parametrize('args', [
    {'filters': ''},
    {'filters': 'cap_large', 'signal': 'not-a-signal'},
    {'filters': 'cap_large', 'order': 'not-an-order'},
    {'filters': 'cap_large', 'max_results': 0},
])
@pytest.mark.asyncio
async def test_custom_screener_invalid_input_is_mcp_error(args):
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool('custom_screener', args)
    assert result.isError is True


@pytest.mark.asyncio
async def test_valid_empty_filings_remain_success(monkeypatch):
    monkeypatch.setattr(server.finviz_sec, 'api_key', 'fixture')
    response = requests.Response()
    response.status_code = 200
    response._content = b'Filing Date,Form\n'
    monkeypatch.setattr(server.finviz_sec, '_make_request', lambda *a, **kw: response)
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '0000000001')
    monkeypatch.setattr(server.edgar_client.client, 'get_submissions', lambda **kw: {'filings': {'recent': {'form': []}}})
    async with create_connected_server_and_client_session(server.server) as client:
        for tool in ('get_sec_filing_summary', 'get_edgar_company_filings'):
            result = await client.call_tool(tool, {'ticker': 'NVDA'})
            assert result.isError is False
            assert 'No ' in str(result.content)


@pytest.mark.parametrize('tool,method,args,payload', [
    ('get_edgar_company_filings', 'get_submissions', {'ticker': 'NVDA'}, {}),
    ('get_edgar_company_filings', 'get_submissions', {'ticker': 'NVDA'}, {'error': 'fixture outage'}),
    ('get_edgar_company_filings', 'get_submissions', {'ticker': 'NVDA'}, {'filings': {'recent': {'form': ['10-K']}}}),
    ('get_edgar_company_filings', 'get_submissions', {'ticker': 'NVDA'}, {'filings': {'recent': {'form': ['10-K'], 'filingDate': [''], 'accessionNumber': ['001'], 'primaryDocument': ['r.htm']}}}),
    ('get_edgar_company_facts', 'get_company_facts', {'ticker': 'NVDA'}, {'error': 'fixture outage'}),
    ('get_edgar_company_concept', 'get_company_concept', {'ticker': 'NVDA', 'concept': 'Assets'}, {}),
])
@pytest.mark.asyncio
async def test_malformed_edgar_envelopes_are_not_empty_success(monkeypatch, tool, method, args, payload):
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '0000000001')
    monkeypatch.setattr(server.edgar_client.client, method, lambda *a, **kw: payload)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool(tool, args)
    assert result.isError is True
    assert 'PROVIDER_CONTRACT' in str(result.content)


@pytest.mark.parametrize('body', [
    'Filing Date,Form\n,10-K\n',
    'Filing Date,Form\n2026-09-01,\n',
    'Filing Date,Form\n2026-09-01,10-K\n,10-Q\n',
])
@pytest.mark.asyncio
async def test_sec_malformed_rows_are_not_silently_discarded(monkeypatch, body):
    monkeypatch.setattr(server.finviz_sec, 'api_key', 'fixture')
    response = requests.Response()
    response.status_code = 200
    response._content = body.encode()
    monkeypatch.setattr(server.finviz_sec, '_make_request', lambda *a, **kw: response)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool('get_sec_filing_summary', {'ticker': 'NVDA'})
    assert result.isError is True
    assert 'PROVIDER_CONTRACT' in str(result.content)
