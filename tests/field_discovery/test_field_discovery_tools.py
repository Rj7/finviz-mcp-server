"""Field discovery's complete canonical catalog and typed MCP contract."""

import json
from collections import Counter

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from src.constants import FINVIZ_COMPREHENSIVE_FIELD_MAPPING as CANONICAL_FIELDS
from src.field_discovery.tools import (
    describe_field, get_field_categories, list_available_fields,
    register_field_discovery_tools, search_fields, validate_fields,
)
from src.tool_policy import ContractFastMCP


def payload(result):
    assert isinstance(result, CallToolResult)
    assert result.isError is False
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    data = result.structuredContent
    # Text-only clients must receive the same data as structured clients.
    assert json.loads(result.content[0].text.split("\n", 1)[1]) == data
    return data


def collect_fields(tool=list_available_fields, **arguments):
    fields, cursors = [], set()
    cursor = snapshot = None
    while True:
        data = payload(tool(cursor=cursor, **arguments))
        page = data["page"]
        assert page["returned"] == len(data["fields"])
        assert page["returned"] <= arguments.get("limit", 25)
        assert page["truncated"] == page["has_more"]
        snapshot = snapshot or page["snapshot"]
        assert page["snapshot"] == snapshot
        fields.extend(data["fields"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            assert len(fields) == data["total"]
            return fields
        cursor = page["next_cursor"]
        assert cursor and cursor not in cursors
        cursors.add(cursor)
        assert len(cursors) <= len(CANONICAL_FIELDS)


class TestListAvailableFields:
    def test_default_page_has_25_fields_and_continuation(self):
        data = payload(list_available_fields())
        assert len(data["fields"]) == 25
        assert data["total"] == len(CANONICAL_FIELDS)
        assert data["page"]["has_more"] is True
        assert data["page"]["next_cursor"]

    @pytest.mark.parametrize("limit", [1, 17, 50])
    def test_every_canonical_field_is_reachable_once(self, limit):
        fields = collect_fields(limit=limit)
        assert [field["name"] for field in fields] == sorted(CANONICAL_FIELDS)
        for field in fields:
            source = CANONICAL_FIELDS[field["name"]]
            assert field["label"] == source["csv_name"]
            assert field["column_id"] == source["column_id"]

    @pytest.mark.parametrize("limit", [0, -1, 101])
    def test_rejects_invalid_page_size(self, limit):
        with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
            list_available_fields(limit=limit)

    def test_rejects_malformed_cursor(self):
        with pytest.raises(ToolError, match="INVALID_CURSOR"):
            list_available_fields(cursor="not-a-cursor")

    def test_cursor_cannot_be_reused_for_another_tool(self):
        cursor = payload(list_available_fields(limit=1))["page"]["next_cursor"]
        with pytest.raises(ToolError, match="INVALID_CURSOR"):
            search_fields("price", cursor=cursor)

    def test_catalog_change_invalidates_existing_cursor(self, monkeypatch):
        cursor = payload(list_available_fields(limit=1))["page"]["next_cursor"]
        monkeypatch.setitem(CANONICAL_FIELDS, "price", {
            **CANONICAL_FIELDS["price"], "csv_name": "Changed provider label",
        })
        with pytest.raises(ToolError, match="STALE_CURSOR"):
            list_available_fields(cursor=cursor)


class TestGetFieldCategories:
    def test_categories_partition_the_complete_catalog(self):
        data = payload(get_field_categories())
        counts = Counter(field["category"] for field in collect_fields())
        assert data["categories"] == dict(counts)
        assert data["total"] == sum(counts.values()) == len(CANONICAL_FIELDS)
        assert set(counts) == {
            "Basic Information", "Valuation Metrics", "Performance Metrics",
            "Technical Indicators", "Fundamental Data", "Earnings & Growth",
            "ETF Specific", "News & Sentiment", "Trading Data",
        }


class TestDescribeField:
    @pytest.mark.parametrize("name,unit,kind", [
        ("ticker", None, "string"),
        ("price", "USD per share", "number"),
        ("market_cap", "USD millions", "number"),
        ("pe_ratio", "ratio", "number"),
        ("volume", "shares", "number"),
        ("avg_volume", None, "provider native; not verified"),
        ("dividend_yield", None, "provider native; not verified"),
    ])
    def test_preserves_source_metadata_and_verified_units(self, name, unit, kind):
        data = payload(describe_field(name))
        assert data["name"] == name
        assert data["label"] == CANONICAL_FIELDS[name]["csv_name"]
        assert data["column_id"] == CANONICAL_FIELDS[name]["column_id"]
        assert data["unit"] == unit
        assert data["value_type"] == kind

    def test_unknown_field_is_an_actionable_error(self):
        with pytest.raises(ToolError, match="UNKNOWN_FIELD.*search_fields"):
            describe_field("invalid_field_name")

    def test_all_listed_fields_have_identical_descriptions(self):
        for field in collect_fields():
            assert payload(describe_field(field["name"])) == field


class TestSearchFields:
    @pytest.mark.parametrize("keyword", ["growth", "ratio", "Price", "P/E"])
    def test_search_returns_all_name_and_label_matches(self, keyword):
        matches = collect_fields(search_fields, keyword=keyword, limit=2)
        expected = sorted(
            name for name, raw in CANONICAL_FIELDS.items()
            if keyword.lower() in name.lower() or keyword.lower() in raw["csv_name"].lower()
        )
        assert [field["name"] for field in matches] == expected
        assert expected

    def test_case_insensitive_search(self):
        lower = collect_fields(search_fields, keyword="growth")
        assert collect_fields(search_fields, keyword="GROWTH") == lower
        assert collect_fields(search_fields, keyword="Growth") == lower

    def test_category_filter_excludes_matches_in_other_categories(self):
        fields = collect_fields(search_fields, keyword="s", category="basic information", limit=1)
        names = {field["name"] for field in fields}
        assert names == {"sector", "industry"}
        assert {field["category"] for field in fields} == {"Basic Information"}

    def test_no_matches_is_an_empty_terminal_page(self):
        data = payload(search_fields("xyz123nonexistent"))
        assert data["fields"] == []
        assert data["total"] == data["page"]["returned"] == 0
        assert data["page"]["has_more"] is False
        assert data["page"]["next_cursor"] is None

    @pytest.mark.parametrize("changed", [{"keyword": "ratio"}, {"category": "Valuation Metrics"}])
    def test_cursor_rejects_changed_search_arguments(self, changed):
        cursor = payload(search_fields("growth", limit=1))["page"]["next_cursor"]
        with pytest.raises(ToolError, match="INVALID_CURSOR"):
            search_fields(**{"keyword": "growth", **changed, "cursor": cursor})


class TestValidateFields:
    def test_accepts_every_canonical_field_in_bounded_batches(self):
        names = list(CANONICAL_FIELDS)
        for start in range(0, len(names), 100):
            requested = names[start:start + 100]
            assert payload(validate_fields(requested)) == {
                "all_valid": True, "valid_fields": requested,
                "invalid_fields": [], "suggestions": {},
            }

    def test_separates_valid_and_invalid_names_and_suggests_canonical_keys(self):
        data = payload(validate_fields(["ticker", "pe_rato", "company", "xyz123nonexistent"]))
        assert data["all_valid"] is False
        assert data["valid_fields"] == ["ticker", "company"]
        assert data["invalid_fields"] == ["pe_rato", "xyz123nonexistent"]
        assert "pe_ratio" in data["suggestions"]["pe_rato"]
        assert data["suggestions"]["xyz123nonexistent"] == []
        assert all(name in CANONICAL_FIELDS for values in data["suggestions"].values() for name in values)


class TestMCPServerIntegration:
    @pytest.fixture
    def discovery_server(self):
        server = ContractFastMCP("Field discovery contract tests")
        register_field_discovery_tools(server)
        return server

    @pytest.mark.asyncio
    async def test_registers_all_five_callable_tools(self, discovery_server):
        tools = await discovery_server.list_tools()
        assert {tool.name for tool in tools} == {
            "list_available_fields", "get_field_categories", "describe_field",
            "search_fields", "validate_fields",
        }
        result = await discovery_server.call_tool("describe_field", {"field_name": "pe_ratio"})
        assert payload(result)["category"] == "Valuation Metrics"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,arguments", [
        ("search_fields", {"keyword": ""}),
        ("validate_fields", {"field_names": []}),
        ("validate_fields", {"field_names": ["ticker"] * 101}),
        ("list_available_fields", {"limit": 101}),
    ])
    async def test_schema_rejects_out_of_bounds_inputs(self, discovery_server, name, arguments):
        with pytest.raises(ToolError, match="validation error"):
            await discovery_server.call_tool(name, arguments)
