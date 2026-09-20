from types import SimpleNamespace
import pytest
from src.finviz_client.options import FinvizOptionsClient

def client_for(csv):
    client = FinvizOptionsClient(api_key='fixture')
    client._make_request = lambda *a, **kw: SimpleNamespace(text=csv)
    return client

def test_side_filter_uses_contract_identity_not_delta():
    csv = 'Contract Name,Expiry,Type,Strike,Delta\nNVDA260925C00100000,2026-09-25,Call,100,-0.1\nNVDA260925P00100000,2026-09-25,Put,100,0.1\n'
    assert [r['contract'] for r in client_for(csv).get_options_chain('NVDA','call')] == ['NVDA260925C00100000']
    assert [r['contract'] for r in client_for(csv).get_options_chain('NVDA','put')] == ['NVDA260925P00100000']

@pytest.mark.parametrize('csv', ['<html>sign in</html>', '', 'oops\n123\n', 'Strike,Delta\n100,0.5\n'])
def test_bad_provider_data_is_not_successful_empty(csv):
    with pytest.raises(Exception, match='UPSTREAM|PROVIDER'):
        client_for(csv).get_options_chain('NVDA')

def test_valid_header_only_csv_is_empty():
    assert client_for('Contract Name,Expiry,Type,Strike\n').get_options_chain('NVDA') == []

def test_tool_bounds_options_and_discloses_expiry(monkeypatch):
    from src import server
    rows=[{'contract':f'NVDA991231C{i:08d}','expiration':'2099-12-31','type':'call','strike':i,'bid':1.0,'as_of':None} for i in range(160)]
    monkeypatch.setattr(server.finviz_options,'get_options_chain',lambda *a,**kw:rows)
    result=server.get_options_chain('NVDA')
    assert len(result.structuredContent['contracts']) == 25
    assert result.structuredContent['selected_expiration'] == '2099-12-31'
    assert result.structuredContent['page']['has_more'] is True
    cursor=result.structuredContent['page']['next_cursor']
    second=server.get_options_chain('NVDA',cursor=cursor)
    assert second.structuredContent['contracts'][0]['strike']==25
    assert len(result.model_dump_json().encode()) <= 32768

def test_tool_error_reaches_mcp_boundary(monkeypatch):
    import asyncio
    from mcp.server.fastmcp.exceptions import ToolError
    from src import server
    def fail(*a,**kw): raise ValueError('UPSTREAM_AUTH: fixture 403')
    monkeypatch.setattr(server.finviz_options,'get_options_chain',fail)
    with pytest.raises(ToolError,match='UPSTREAM_AUTH'):
        asyncio.run(server.server.call_tool('get_options_chain',{'ticker':'NVDA'}))
