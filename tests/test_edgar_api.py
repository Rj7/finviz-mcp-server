"""EDGAR behavior against fixed provider responses; no swallowed failures."""
import requests
from src.finviz_client.edgar_client import EdgarAPIClient

def test_edgar_client_basic(monkeypatch):
    client = EdgarAPIClient()
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"0":{"ticker":"AAPL","cik_str":320193}}'
    monkeypatch.setattr(client.session, 'get', lambda *a, **kw: response)
    assert client._get_cik_from_ticker('aapl') == '0000320193'
    assert client._get_cik_from_ticker('ZZZZZ') is None

def test_company_concept(monkeypatch):
    client = EdgarAPIClient()
    monkeypatch.setattr(client, '_get_cik_from_ticker', lambda _: '0000320193')
    def provider(*, cik, taxonomy, tag):
        assert (cik, taxonomy, tag) == ('0000320193', 'us-gaap', 'Assets')
        return {'units': {'USD': [{'end': '2026-06-30', 'val': 123000000}]}}
    monkeypatch.setattr(client.client, 'get_company_concept', provider)
    assert client.get_company_concept('AAPL', 'Assets')['units']['USD'] == [
        {'end': '2026-06-30', 'val': 123000000}]
