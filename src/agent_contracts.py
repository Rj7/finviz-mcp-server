"""Shared wire contracts: bounded, revision-bound pages and truthful errors."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

BUDGET = 32768

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def page_position(tool, query, snapshot, cursor):
    if not cursor:
        return 0
    try:
        if len(cursor)>4096: raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
        if value['v']!=1 or value['tool']!=tool or value['query']!=digest(query) or type(value['offset']) is not int or value['offset']<0:
            raise ValueError()
        if value['snapshot']!=snapshot:
            raise ToolError('STALE_CURSOR: provider data changed; restart without cursor')
        return value['offset']
    except ToolError: raise
    except Exception as exc:
        raise ToolError('INVALID_CURSOR: keep original arguments or restart without cursor') from exc

def page_info(tool,query,snapshot,offset,count,total):
    end=offset+count
    token={'v':1,'tool':tool,'query':digest(query),'snapshot':snapshot,'offset':end}
    return {'returned':count,'has_more':end<total,'next_cursor':base64.urlsafe_b64encode(json.dumps(token).encode()).decode().rstrip('=') if end<total else None,'snapshot':snapshot,'truncated':end<total}

class Page(BaseModel):
    returned: int
    has_more: bool
    next_cursor: Optional[str]
    snapshot: str
    truncated: bool

def tool_result(data,summary='Bounded provider result',is_error=False):
    if isinstance(data,BaseModel): data=data.model_dump(mode='json')
    response=CallToolResult(content=[TextContent(type='text',text=summary+'\n'+json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False))],structuredContent=data,isError=is_error)
    if len(response.model_dump_json(exclude_none=True).encode())>BUDGET:
        raise ToolError('RESPONSE_TOO_LARGE: lower limit/max_length or narrow filters; no data was silently clipped')
    return response

def provider_error(exc):
    # Never expose requests exception strings: their URL can contain auth keys.
    import requests
    if isinstance(exc,requests.Timeout): return ToolError('UPSTREAM_TIMEOUT: provider timed out; retry later')
    if isinstance(exc,requests.HTTPError):
        status=exc.response.status_code if exc.response is not None else None
        if status in (401,403): return ToolError('UPSTREAM_AUTH: provider denied access; check subscription/credentials')
        if status==429:
            retry=exc.response.headers.get('Retry-After','unspecified')
            return ToolError(f'RATE_LIMITED: wait before retrying; retry_after={retry}')
        return ToolError('UPSTREAM_UNAVAILABLE: provider request failed; retry later')
    if isinstance(exc,requests.RequestException): return ToolError('UPSTREAM_UNAVAILABLE: provider connection failed; retry later')
    return ToolError(str(exc)[:1500])
