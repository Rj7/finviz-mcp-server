"""Offline by default: an accidentally unmocked provider call must fail loudly."""
import pytest
import requests


def pytest_addoption(parser):
    parser.addoption('--live', action='store_true', help='Opt in to live-provider tests')


def pytest_configure(config):
    config.addinivalue_line('markers', 'live: explicitly opt-in external provider checks')


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--live'):
        for item in items:
            if item.get_closest_marker('live'):
                item.add_marker(pytest.mark.skip(reason='Live provider check; opt in with --live'))


@pytest.fixture(autouse=True)
def block_unmocked_provider_requests(monkeypatch, request):
    if request.node.get_closest_marker('live') and request.config.getoption('--live'):
        return

    def blocked(*args, **kwargs):
        # pytest.fail is not caught by application `except Exception` handlers.
        # Never print URLs/kwargs: provider credentials can be query parameters.
        pytest.fail('Unmocked external HTTP request in an offline test', pytrace=False)

    monkeypatch.setattr(requests.sessions.Session, 'request', blocked)


@pytest.fixture(autouse=True)
def restore_provider_singletons(monkeypatch):
    # Monkeypatching an inherited method on an instance can restore a bound
    # method as an instance attribute, shadowing a later class-level patch.
    # The server owns long-lived clients; each test must start with the same
    # attribute layout, not artifacts left by the previous test's mocks.
    from src import server
    clients = [server.finviz_client, server.finviz_screener, server.finviz_news,
               server.finviz_sector, server.finviz_sec, server.finviz_options,
               server.edgar_client, server.edgar_client.client]
    snapshots = [(client, vars(client).copy()) for client in clients]
    yield
    monkeypatch.undo()
    for client, attributes in snapshots:
        vars(client).clear()
        vars(client).update(attributes)
