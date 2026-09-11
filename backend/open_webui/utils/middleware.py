import ast
import asyncio
import base64
import copy
import hashlib
import html
import inspect
import json
import logging
import os
import random
import re
import sys
import textwrap
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from uuid import uuid4

import aiohttp
from aiocache import cached
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from open_webui.config import (
    CACHE_DIR,
    CODE_INTERPRETER_BLOCKED_MODULES,
    CODE_INTERPRETER_PYODIDE_PROMPT,
    DEFAULT_CODE_INTERPRETER_PROMPT,
    DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
)
from open_webui.constants import TASKS
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT_SOCK_READ,
    BYPASS_MODEL_ACCESS_CONTROL,
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS,
    CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
    ENABLE_API_OUTLET_FILTERS,
    ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION,
    ENABLE_PLUGINS,
    ENABLE_QUERIES_CACHE,
    ENABLE_REALTIME_CHAT_SAVE,
    ENABLE_RESPONSES_API_STATEFUL,
    GLOBAL_LOG_LEVEL,
    RAG_SYSTEM_CONTEXT,
    SSE_KEEPALIVE_INTERVAL,
)
from open_webui.events import EVENTS, publish_event
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.models.functions import Functions
from open_webui.models.models import Models
from open_webui.models.notes import Notes
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.users import UserModel, Users
from open_webui.retrieval.utils import get_sources_from_items
from open_webui.routers.images import (
    CreateImageForm,
    EditImageForm,
    image_edits,
    image_generations,
)
from open_webui.routers.memories import QueryMemoryForm, query_memory
from open_webui.utils.pii_chunking import (
    PII_INLET_CHUNK_CHARS,
    PII_INLET_CONCURRENCY,
    PII_INLET_TOTAL_BUDGET_S,
    estimated_masking_seconds,
    split_text_for_pii,
)
from open_webui.routers.pipelines import (
    get_sorted_filters,
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
    resolve_pii_masking_enforced,
)
from open_webui.routers.retrieval import (
    SearchForm,
    process_web_search,
)
from open_webui.routers.tasks import (
    generate_chat_tags,
    generate_follow_ups,
    generate_image_prompt,
    generate_queries,
    generate_title,
)
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
)
from open_webui.models.access_grants import AccessGrants
from open_webui.utils.access_control import has_connection_access, has_permission
from open_webui.utils.access_control.files import (
    get_accessible_folder_files,
    get_owner_accessible_folder_files,
)
from open_webui.utils.access_control.folders import has_folder_access
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.context_compaction import compact_messages_for_request
from open_webui.utils.files import (
    convert_markdown_base64_images,
    get_file_url_from_base64,
    get_image_base64_from_url,
    get_image_url_from_base64,
)
from open_webui.utils.filter import (
    FilterContext,
    get_filter_functions,
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.memory import add_memory_context, review_memory_after_turn
from open_webui.utils.misc import (
    add_or_update_system_message,
    add_or_update_user_message,
    convert_logit_bias_input_to_json,
    convert_output_to_messages,
    deep_update,
    extract_urls,
    get_content_from_message,
    get_last_assistant_message,
    get_last_user_message,
    get_last_user_message_item,
    get_message_list,
    get_output_text,
    get_system_message,
    is_string_allowed,
    merge_system_messages,
    prepend_to_first_user_message_content,
    replace_system_message_content,
    set_last_user_message_content,
    strip_empty_content_blocks,
)
from open_webui.utils.payload import apply_system_prompt_to_body, resolve_system_prompt
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.response import merge_usage, normalize_usage
from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.task import (
    get_task_model_id,
    rag_template,
    tools_function_calling_generation_template,
)
from open_webui.utils.tools import (
    build_tool_server_headers,
    get_attached_knowledge,
    get_builtin_tools,
    get_terminal_tools,
    get_tools,
    get_updated_tool_function,
)
from open_webui.utils.webhook import post_webhook
from starlette.responses import JSONResponse, Response, StreamingResponse


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


from open_webui.utils.sse import _KEEPALIVE, _keepalive_iter


async def publish_chat_finished_event(
    request: Request, user: UserModel, metadata: dict, title: str, content: str, output: list | None = None
):
    chat_id = metadata.get('chat_id')
    if getattr(request.state, 'internal', False) is True or not is_saved_chat_id(chat_id):
        return

    content = content or get_output_text(output)
    webui_url = request.app.state.config.WEBUI_URL
    await publish_event(
        request,
        EVENTS.CHAT_FINISHED,
        actor=user,
        subject_id=chat_id,
        subject_type='chat',
        data={
            'user_id': user.id,
            'chat_id': chat_id,
            'message_id': metadata.get('message_id'),
            'model_id': metadata.get('model_id'),
            'title': title,
            'url': f'{webui_url}/c/{chat_id}' if webui_url else f'/c/{chat_id}',
            'message': content,
        },
        message=title or 'Chat finished',
    )
    event_emitter = await get_event_emitter(metadata, update_db=False)
    if event_emitter:
        folder_id = metadata.get('folder_id') or await Chats.get_chat_folder_id(chat_id, metadata.get('user_id'))
        await event_emitter({'type': 'chat:list', 'data': {'chat_id': chat_id, 'folder_id': folder_id}})

# We believe in one maker of all models, seen and unseen,
# and in the reasoning which proceeds from the architect.
# We look for the resurrection of dead processes and the
# inference of the world to come.
DEFAULT_REASONING_TAGS = [
    ('<think>', '</think>'),
    ('<thinking>', '</thinking>'),
    ('<reason>', '</reason>'),
    ('<reasoning>', '</reasoning>'),
    ('<thought>', '</thought>'),
    ('<Thought>', '</Thought>'),
    ('<|begin_of_thought|>', '<|end_of_thought|>'),
    ('◁think▷', '◁/think▷'),
]

DEFAULT_SOLUTION_TAGS = [('<|begin_of_solution|>', '<|end_of_solution|>')]
DEFAULT_CODE_INTERPRETER_TAGS = [('<code_interpreter>', '</code_interpreter>')]


def _start_tag_pattern(start_tag: str) -> str:
    if start_tag.startswith('<') and start_tag.endswith('>'):
        return rf'<{re.escape(start_tag[1:-1])}(\s.*?)?>'
    return re.escape(start_tag)


def output_id(prefix: str) -> str:
    """Generate OR-style ID: prefix + 24-char hex UUID."""
    return f'{prefix}_{uuid4().hex[:24]}'


def merge_streamed_reasoning_details(target: list, details) -> None:
    items = details if isinstance(details, list) else [details]
    for item in items:
        if not isinstance(item, dict):
            continue

        index = item.get('index')
        existing = (
            next((detail for detail in target if detail.get('index') == index), None)
            if isinstance(index, int)
            else None
        )
        if existing is None:
            target.append(dict(item))
            continue

        for key, value in item.items():
            if key in ('text', 'summary') and isinstance(value, str) and isinstance(existing.get(key), str):
                existing[key] += value
            else:
                existing[key] = value


def _split_tool_calls(
    tool_calls: list[dict],
) -> list[dict]:
    """Expand tool calls whose arguments contain multiple back-to-back JSON objects.

    Some models (e.g. GPT-5.4) send multiple complete JSON argument objects
    under the same tool call index, producing concatenated invalid JSON like:
        '{"query":"A","count":5}{"query":"B","count":5}'

    Each such tool call is split into separate entries so each gets executed
    independently. Single-object arguments pass through unchanged.
    """

    def split_json_objects(raw: str) -> list[str]:
        if not isinstance(raw, str):
            raw = '' if raw is None else json.dumps(raw)

        decoder = json.JSONDecoder()
        results = []
        position = 0

        while position < len(raw):
            while position < len(raw) and raw[position].isspace():
                position += 1
            if position >= len(raw):
                break
            try:
                _, end = decoder.raw_decode(raw, position)
                results.append(raw[position:end].strip())
                position = end
            except json.JSONDecodeError:
                return [raw]

        return results or [raw]

    expanded = []
    for tool_call in tool_calls:
        function = tool_call.setdefault('function', {})
        arguments = function.get('arguments')
        if not isinstance(arguments, str):
            arguments = '' if arguments is None else json.dumps(arguments)
            function['arguments'] = arguments
        split_arguments = split_json_objects(arguments)

        if len(split_arguments) <= 1:
            expanded.append(tool_call)
        else:
            for argument in split_arguments:
                cloned = copy.deepcopy(tool_call)
                cloned['id'] = f'call_{uuid4().hex[:24]}'
                cloned['function']['arguments'] = argument
                expanded.append(cloned)

    return expanded


def get_citation_source_from_tool_result(
    tool_name: str, tool_params: dict, tool_result: str, tool_id: str = ''
) -> list[dict]:
    """
    Parse a tool's result and convert it to source dicts for citation display.

    Follows the source format conventions from get_sources_from_items:
    - source: file/item info object with id, name, type
    - document: list of document contents
    - metadata: list of metadata objects with source, file_id, name fields

    Returns a list of sources (usually one, but query_knowledge_files/query_chat_files may return multiple).
    """
    _EXPECTS_LIST = {'search_web', 'query_knowledge_files', 'query_chat_files'}
    _EXPECTS_DICT = {'view_knowledge_file', 'view_file'}

    try:
        try:
            tool_result = JSONCodec.loads(tool_result)
        except (json.JSONDecodeError, TypeError):
            pass  # keep tool_result as-is (e.g. fetch_url returns plain text)
        if isinstance(tool_result, dict) and 'error' in tool_result:
            return []

        # Validate tool_result type based on what the branch expects
        if tool_name in _EXPECTS_LIST and not isinstance(tool_result, list):
            return []
        elif tool_name in _EXPECTS_DICT and not isinstance(tool_result, dict):
            return []

        if tool_name == 'search_web':
            # Parse JSON array: [{"title": "...", "link": "...", "snippet": "..."}]
            results = tool_result
            documents = []
            metadata = []

            for result in results:
                title = result.get('title', '')
                link = result.get('link', '')
                snippet = result.get('snippet', '')

                documents.append(f'{title}\n{snippet}')
                metadata.append(
                    {
                        'source': link,
                        'name': title,
                        'url': link,
                    }
                )

            return [
                {
                    'source': {'name': 'search_web', 'id': 'search_web'},
                    'document': documents,
                    'metadata': metadata,
                }
            ]

        elif tool_name in ('view_knowledge_file', 'view_file'):
            file_data = tool_result
            filename = file_data.get('filename', 'Unknown File')
            file_id = file_data.get('id', '')
            knowledge_name = file_data.get('knowledge_name', '')

            return [
                {
                    'source': {
                        'id': file_id,
                        'name': filename,
                        'type': 'file',
                    },
                    'document': [file_data.get('content', '')],
                    'metadata': [
                        {
                            'file_id': file_id,
                            'name': filename,
                            'source': filename,
                            **({'knowledge_name': knowledge_name} if knowledge_name else {}),
                        }
                    ],
                }
            ]

        elif tool_name == 'fetch_url':
            url = tool_params.get('url', '')
            content = tool_result if isinstance(tool_result, str) else str(tool_result)
            snippet = content[:500] + ('...' if len(content) > 500 else '')

            return [
                {
                    'source': {'name': url or 'fetch_url', 'id': url or 'fetch_url'},
                    'document': [snippet],
                    'metadata': [
                        {
                            'source': url,
                            'name': url,
                            'url': url,
                        }
                    ],
                }
            ]

        elif tool_name in ('query_knowledge_files', 'query_chat_files'):
            chunks = tool_result

            # Group chunks by source for better citation display
            # Each unique source becomes a separate source entry
            sources_by_file = {}

            for chunk in chunks:
                source_name = chunk.get('source', 'Unknown')
                file_id = chunk.get('file_id', '')
                note_id = chunk.get('note_id', '')
                chunk_type = chunk.get('type', 'file')
                content = chunk.get('content', '')

                # Use file_id or note_id as the key
                key = file_id or note_id or source_name

                if key not in sources_by_file:
                    sources_by_file[key] = {
                        'source': {
                            'id': file_id or note_id,
                            'name': source_name,
                            'type': chunk_type,
                        },
                        'document': [],
                        'metadata': [],
                    }

                sources_by_file[key]['document'].append(content)
                sources_by_file[key]['metadata'].append(
                    {
                        'file_id': file_id,
                        'name': source_name,
                        'source': source_name,
                        **({'note_id': note_id} if note_id else {}),
                    }
                )

            # Return all grouped sources as a list
            if sources_by_file:
                return list(sources_by_file.values())

            # Empty result fallback
            return []

        else:
            # Fallback for other tools
            return [
                {
                    'source': {
                        'name': tool_name,
                        'type': 'tool',
                        'id': tool_id or tool_name,
                    },
                    'document': [str(tool_result)],
                    'metadata': [{'source': tool_name, 'name': tool_name}],
                }
            ]
    except Exception as e:
        log.exception(f'Error parsing tool result for {tool_name}: {e}')
        return [
            {
                'source': {'name': tool_name, 'type': 'tool'},
                'document': [str(tool_result)],
                'metadata': [{'source': tool_name}],
            }
        ]


def split_content_and_whitespace(content):
    content_stripped = content.rstrip()
    original_whitespace = content[len(content_stripped) :] if len(content) > len(content_stripped) else ''
    return content_stripped, original_whitespace


def is_opening_code_block(content):
    backtick_segments = content.split('```')
    # Even number of segments means the last backticks are opening a new block
    return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0


_OPENAI_TOOL_DISPLAY_NAMES = {
    'web_search_call': 'Web Search',
    'file_search_call': 'File Search',
    'computer_call': 'Computer Use',
}


def _render_openai_tool_call_handler(item: dict, done: bool) -> str:
    """Render an OpenAI Responses API server-side tool item as a <details> block.

    Handles web_search_call, file_search_call, and computer_call items whose
    schemas are defined in the openai-python SDK (generated from OpenAPI spec).
    """
    item_type = item.get('type', '')
    call_id = item.get('id', '')
    display_name = _OPENAI_TOOL_DISPLAY_NAMES.get(item_type, item_type)

    # Build a short summary of what the tool did
    summary = ''
    if item_type == 'web_search_call':
        action = item.get('action', {})
        if isinstance(action, dict):
            atype = action.get('type', '')
            if atype == 'search':
                queries = action.get('queries') or []
                query = action.get('query', '')
                summary = (
                    f'Search: {", ".join(str(q) for q in queries)}'
                    if queries
                    else (f'Search: {query}' if query else '')
                )
            elif atype == 'open_page':
                summary = f'Open page: {action.get("url", "")}' if action.get('url') else ''
            elif atype == 'find_in_page':
                summary = f'Find in page: {action.get("pattern", "")}' if action.get('pattern') else ''
    elif item_type == 'file_search_call':
        queries = item.get('queries', [])
        if queries:
            summary = f'Queries: {", ".join(str(q) for q in queries)}'
    elif item_type == 'computer_call':
        action = item.get('action')
        actions = item.get('actions')
        if isinstance(action, dict):
            summary = f'Action: {action.get("type", "unknown")}'
        elif isinstance(actions, list) and actions:
            summary = f'Actions: {", ".join(a.get("type", "?") for a in actions if isinstance(a, dict))}'

    escaped_name = html.escape(display_name)
    if done:
        return f'<details type="tool_calls" done="true" id="{call_id}" name="{escaped_name}" arguments="">\n<summary>Tool Executed</summary>\n{html.escape(summary)}\n</details>\n'
    return f'<details type="tool_calls" done="false" id="{call_id}" name="{escaped_name}" arguments="">\n<summary>Executing...</summary>\n</details>\n'


def serialize_output(output: list) -> str:
    """
    Convert OR-aligned output items to HTML for display.
    For LLM consumption, use convert_output_to_messages() instead.
    """
    parts: list[str] = []

    # First pass: collect function_call_output items by call_id for lookup
    tool_outputs = {}
    for item in output:
        if item.get('type') == 'function_call_output':
            tool_outputs[item.get('call_id')] = item

    # Second pass: render items in order
    for idx, item in enumerate(output):
        item_type = item.get('type', '')

        if item_type == 'message':
            for content_part in item.get('content', []):
                if 'text' in content_part:
                    text = content_part.get('text', '').strip()
                    if text:
                        parts.append(text)

        elif item_type == 'function_call':
            call_id = item.get('call_id', '')
            name = item.get('name', '')
            arguments = item.get('arguments', '')

            result_item = tool_outputs.get(call_id)
            if result_item:
                result_parts: list[str] = []
                for result_output in result_item.get('output', []):
                    if 'text' in result_output:
                        output_text = result_output.get('text', '')
                        result_parts.append(str(output_text) if not isinstance(output_text, str) else output_text)
                result_text = ''.join(result_parts)
                files = result_item.get('files')
                embeds = result_item.get('embeds', '')

                parts.append(
                    f'<details type="tool_calls" done="true" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}" files="{html.escape(json.dumps(files)) if files else ""}" embeds="{html.escape(json.dumps(embeds))}">\n<summary>Tool Executed</summary>\n{html.escape(json.dumps(result_text, ensure_ascii=False))}\n</details>'
                )
            else:
                parts.append(
                    f'<details type="tool_calls" done="false" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}">\n<summary>Executing...</summary>\n</details>'
                )

        elif item_type == 'function_call_output':
            # Already handled inline with function_call above
            pass

        elif item_type in _OPENAI_TOOL_DISPLAY_NAMES:
            status = item.get('status', 'in_progress')
            done = status in ('completed', 'failed', 'incomplete') or idx != len(output) - 1
            parts.append(_render_openai_tool_call_handler(item, done).rstrip('\n'))

        elif item_type == 'reasoning':
            reasoning_parts: list[str] = []
            # Check for 'summary' (new structure) or 'content' (legacy/fallback)
            source_list = item.get('summary', []) or item.get('content', [])
            for content_part in source_list:
                if 'text' in content_part:
                    reasoning_parts.append(content_part.get('text', ''))
                elif 'summary' in content_part:  # Handle potential nested logic if any
                    pass

            reasoning_content = ''.join(reasoning_parts).strip()

            duration = item.get('duration')
            status = item.get('status', 'in_progress')

            # Infer completion: if this reasoning item is NOT the last item,
            # render as done (a subsequent item means reasoning is complete)
            is_last_item = idx == len(output) - 1

            display = html.escape(
                '\n'.join(
                    (f'> {line}' if not line.startswith('>') else line) for line in reasoning_content.splitlines()
                )
            )

            if status == 'completed' or duration is not None or not is_last_item:
                parts.append(
                    f'<details type="reasoning" done="true" duration="{duration or 0}">\n<summary>Thought for {duration or 0} seconds</summary>\n{display}\n</details>'
                )
            else:
                parts.append(
                    f'<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n{display}\n</details>'
                )

        elif item_type == 'open_webui:code_interpreter':
            # Code interpreter needs to inspect/mutate prior accumulated content
            # to strip trailing unclosed code fences — materialize only here.
            content = '\n'.join(parts)
            content_stripped, original_whitespace = split_content_and_whitespace(content)
            if is_opening_code_block(content_stripped):
                content = content_stripped.rstrip('`').rstrip() + original_whitespace
            else:
                content = content_stripped + original_whitespace

            # Re-split back into parts list after mutation
            parts = [content] if content else []

            # Render the code_interpreter item as a <details> block
            # so the frontend Collapsible renders "Analyzing..."/"Analyzed".
            code = item.get('code', '').strip()
            lang = item.get('lang', 'python')
            status = item.get('status', 'in_progress')
            duration = item.get('duration')
            is_last_item = idx == len(output) - 1

            # Build inner content: code block
            display = ''
            if code:
                display = f'```{lang}\n{code}\n```'

            # Build output attribute as HTML-escaped JSON for CodeBlock.svelte
            ci_output = item.get('output')
            output_attr = ''
            if ci_output:
                if isinstance(ci_output, dict):
                    output_json = json.dumps(ci_output, ensure_ascii=False)
                else:
                    output_json = json.dumps({'result': str(ci_output)}, ensure_ascii=False)
                output_attr = f' output="{html.escape(output_json)}"'

            if status == 'completed' or duration is not None or not is_last_item:
                parts.append(
                    f'<details type="code_interpreter" done="true" duration="{duration or 0}"{output_attr}>\n<summary>Analyzed</summary>\n{display}\n</details>'
                )
            else:
                parts.append(
                    f'<details type="code_interpreter" done="false"{output_attr}>\n<summary>Analyzing…</summary>\n{display}\n</details>'
                )

    return '\n'.join(parts).strip()


def deep_merge(target, source):
    """
    Merge source into target recursively (returning new structure).
    - Dicts: Recursive merge.
    - Strings: Concatenation.
    - Others: Overwrite.
    """
    if isinstance(target, dict) and isinstance(source, dict):
        new_target = target.copy()
        for k, v in source.items():
            if k in new_target:
                new_target[k] = deep_merge(new_target[k], v)
            else:
                new_target[k] = v
        return new_target
    elif isinstance(target, str) and isinstance(source, str):
        return target + source
    else:
        return source


def handle_responses_streaming_event(
    data: dict,
    current_output: list,
) -> tuple[list, dict | None]:
    """
    Handle Responses API streaming events in a pure functional way.

    Args:
        data: The event data
        current_output: List of output items (treated as immutable)

    Returns:
        tuple[list, dict | None]: (new_output, metadata)
        - new_output: The updated output list.
        - metadata: Metadata to emit (e.g. usage), {} if update occurred, None if skip.
    """
    # Default: no change
    # Note: treating current_output as immutable, but avoiding full deepcopy for perf.
    # We will shallow copy only if we need to modify the list structure or items.

    event_type = data.get('type', '')

    if event_type == 'response.output_item.added':
        item = data.get('item', {})
        if item:
            new_output = list(current_output)
            new_output.append(item)
            return new_output, None
        return current_output, None

    elif event_type == 'response.content_part.added':
        part = data.get('part', {})
        output_index = data.get('output_index', len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            # Copy the item to mutate it
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if 'content' not in item:
                item['content'] = []
            else:
                # Copy content list
                item['content'] = list(item['content'])

            if item.get('type') == 'reasoning':
                # Reasoning items should not have content parts
                pass
            else:
                item['content'].append(part)
            return new_output, None
        return current_output, None

    elif event_type == 'response.reasoning_summary_part.added':
        part = data.get('part', {})
        output_index = data.get('output_index', len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if 'summary' not in item:
                item['summary'] = []
            else:
                item['summary'] = list(item['summary'])

            item['summary'].append(part)
            return new_output, None
        return current_output, None

    elif event_type.startswith('response.') and event_type.endswith('.delta'):
        # Generic Delta Handling
        parts = event_type.split('.')
        if len(parts) >= 3:
            delta_type = parts[1]
            delta = data.get('delta', '')

            output_index = data.get('output_index', len(current_output) - 1)

            if current_output and 0 <= output_index < len(current_output):
                new_output = list(current_output)
                item = new_output[output_index].copy()
                new_output[output_index] = item
                item_type = item.get('type', '')

                # Determine target field and object based on delta_type and item_type
                if delta_type == 'function_call_arguments':
                    key = 'arguments'
                    if item_type == 'function_call':
                        # Function call args are usually strings
                        item[key] = item.get(key, '') + str(delta)
                else:
                    # Generic handling, refined by item type below
                    pass

                    if item_type == 'message':
                        # Message items: "text"/"output_text" -> "text"
                        # "reasoning_text" -> Skipped (should use reasoning item)
                        if delta_type in ['text', 'output_text']:
                            key = 'text'
                        elif delta_type in ['reasoning_text', 'reasoning_summary_text']:
                            # Skip reasoning updates for message items
                            return new_output, None
                        else:
                            key = delta_type

                        content_index = data.get('content_index', 0)
                        if 'content' not in item:
                            item['content'] = []
                        else:
                            item['content'] = list(item['content'])
                        content_list = item['content']

                        while len(content_list) <= content_index:
                            content_list.append({'type': 'text', 'text': ''})

                        # Copy the part to mutate it
                        part = content_list[content_index].copy()
                        content_list[content_index] = part

                        current_val = part.get(key)
                        if current_val is None:
                            # Initialize based on delta type
                            current_val = {} if isinstance(delta, dict) else ''

                        part[key] = deep_merge(current_val, delta)

                    elif item_type == 'reasoning':
                        # Reasoning items: "reasoning_text"/"reasoning_summary_text" -> "text"
                        # "text"/"output_text" -> Skipped (should use message item)
                        if delta_type == 'reasoning_summary_text':
                            # Summary updates -> item['summary']
                            key = 'text'
                            summary_index = data.get('summary_index', 0)
                            if 'summary' not in item:
                                item['summary'] = []
                            else:
                                item['summary'] = list(item['summary'])
                            summary_list = item['summary']

                            while len(summary_list) <= summary_index:
                                summary_list.append({'type': 'summary_text', 'text': ''})

                            part = summary_list[summary_index].copy()
                            summary_list[summary_index] = part

                            target_val = part.get(key, '')
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type == 'reasoning_text':
                            # Reasoning body updates -> item['content']
                            key = 'text'
                            content_index = data.get('content_index', 0)
                            if 'content' not in item:
                                item['content'] = []
                            else:
                                item['content'] = list(item['content'])
                            content_list = item['content']

                            while len(content_list) <= content_index:
                                # Reasoning content parts default to text
                                content_list.append({'type': 'text', 'text': ''})

                            part = content_list[content_index].copy()
                            content_list[content_index] = part

                            target_val = part.get(key, '')
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type in ['text', 'output_text']:
                            return new_output, None
                        else:
                            # Fallback just in case other deltas target reasoning?
                            pass

                    else:
                        # Fallback for other item types
                        if delta_type in ['text', 'output_text']:
                            key = 'text'
                        else:
                            key = delta_type

                        current_val = item.get(key)
                        if current_val is None:
                            current_val = {} if isinstance(delta, dict) else ''
                        item[key] = deep_merge(current_val, delta)

            return new_output, None

    elif event_type.startswith('response.') and event_type.endswith('.done'):
        # Delta Events: response.content_part.done, response.text.done, etc.
        parts = event_type.split('.')
        if len(parts) >= 3:
            type_name = parts[1]

            # 1. Handle specific Delta "done" signals
            if type_name == 'content_part':
                # "Signaling that no further changes will occur to a content part"
                # If payloads contains the full part, we could update it.
                # Usually purely signaling in standard implementation, but we check payload.
                part = data.get('part')
                output_index = data.get('output_index', len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if 'content' in item:
                        item['content'] = list(item['content'])
                        content_index = data.get('content_index', len(item['content']) - 1)
                        if 0 <= content_index < len(item['content']):
                            item['content'][content_index] = part
                            return new_output, {}
                return current_output, None

            elif type_name == 'reasoning_summary_part':
                part = data.get('part')
                output_index = data.get('output_index', len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if 'summary' in item:
                        item['summary'] = list(item['summary'])
                        summary_index = data.get('summary_index', len(item['summary']) - 1)
                        if 0 <= summary_index < len(item['summary']):
                            item['summary'][summary_index] = part
                            return new_output, {}
                return current_output, None

            # 2. Skip Output Item done (handled specifically below)
            if type_name == 'output_item':
                pass

            # 3. Generic Field Done (text.done, audio.done)
            elif type_name not in ['completed', 'failed']:
                output_index = data.get('output_index', len(current_output) - 1)
                if current_output and 0 <= output_index < len(current_output):
                    key = (
                        'text'
                        if type_name
                        in [
                            'text',
                            'output_text',
                            'reasoning_text',
                            'reasoning_summary_text',
                        ]
                        else type_name
                    )
                    if type_name == 'function_call_arguments':
                        key = 'arguments'

                    if key in data:
                        final_value = data[key]
                        new_output = list(current_output)
                        item = new_output[output_index].copy()
                        new_output[output_index] = item
                        item_type = item.get('type', '')

                        if type_name == 'function_call_arguments':
                            if item_type == 'function_call':
                                item['arguments'] = final_value
                        elif item_type == 'message':
                            content_index = data.get('content_index', 0)
                            if 'content' in item:
                                item['content'] = list(item['content'])
                                if len(item['content']) > content_index:
                                    part = item['content'][content_index].copy()
                                    item['content'][content_index] = part
                                    part[key] = final_value
                        elif item_type == 'reasoning':
                            item['status'] = 'completed'
                        else:
                            item[key] = final_value

                        return new_output, {}

        return current_output, None

    elif event_type == 'response.output_item.done':
        # Delta Event: Output item complete
        item = data.get('item')
        output_index = data.get('output_index', len(current_output) - 1)

        new_output = list(current_output)
        if item and 0 <= output_index < len(current_output):
            new_output[output_index] = item
        elif item:
            new_output.append(item)
        return new_output, {}

    elif event_type == 'response.completed':
        # State Machine Event: Completed
        response_data = data.get('response', {})
        final_output = response_data.get('output')

        new_output = final_output if final_output is not None else current_output

        # Ensure reasoning items are marked as completed in the final output
        if new_output:
            for item in new_output:
                if item.get('type') == 'reasoning' and item.get('status') != 'completed':
                    item['status'] = 'completed'

        return new_output, {
            'usage': response_data.get('usage'),
            'done': True,
            'response_id': response_data.get('id'),
        }

    elif event_type == 'response.in_progress':
        # State Machine Event: In Progress
        # We could extract metadata if needed, but for now just acknowledge iteration
        return current_output, None

    elif event_type == 'response.failed':
        # State Machine Event: Failed
        error = data.get('response', {}).get('error', {})
        return current_output, {'error': error}

    else:
        return current_output, None


class PiiMaskingBlockedError(Exception):
    """Fail-closed signal for Task 3.6 file/tool-attachment PII masking.

    Raised when source text could NOT be guaranteed masked (PII pipeline
    unreachable / errored / misconfigured, missing chat_id, or oversized
    input). The request MUST be blocked before reaching the LLM rather than
    forwarding unmasked source text. Deliberately distinct from the fail-OPEN
    behavior of ``process_pipeline_inlet_filter`` (pipelines.py:165-166), which
    logs-and-continues on connection errors.
    """


# DoS guard: max accumulated source text (sum of chunk lengths) per source that
# we will route through the Presidio inlet. Over this -> fail-closed (block).
# Superseded by the shared wall-clock budget (`PII_INLET_TOTAL_BUDGET_S` /
# `max_maskable_chars`). A fixed character wall was the wrong shape for the
# problem: 50 000 characters is ~15 pages, so the identical document was
# refused as an attachment and accepted as a prompt, where the budget allowed
# five times as much. Kept as a name only because a stale import elsewhere
# should fail loudly rather than silently re-introduce the wall.
MAX_SOURCE_TEXT_CHARS = None

# TRAU-513: a single external-pipeline masking call silently truncates its input
# at the pipeline's tokenizer cap (~512 tokens). A source document longer than
# that would lose its tail — PII past the cutoff never reaches analyze(). The
# same text as a PDF (arriving pre-chunked per page, each page < cap) therefore
# masked MORE entities than as one long TXT blob. We split every document into
# pieces well under the cap before masking so coverage is format-independent.
# Kept comfortably below the observed ~2973-char (≈512-token) cutoff; the e2e
# fixture's largest paragraph is ~530 chars, so this only ever breaks on
# blank-line/newline boundaries and never cuts a multi-token entity.
# Sub-chunk size for source text. Kept as an alias of the shared prompt-path
# constant (TRAU-543) rather than a second copy: two independently tunable
# 1800s that must stay equal is a bug waiting for someone to change one.
PII_MASK_CHUNK_CHARS = PII_INLET_CHUNK_CHARS

# Ingest scan ONLY: how many sub-chunk masking POSTs to run concurrently against
# the external pipeline. The scan discards masked text and uses only detection
# spans, so vault races on the synthetic per-file key are harmless and order does
# not matter — concurrency is safe here. It is deliberately NOT applied to the
# chat-time masking path (which must stay sequential to keep masked-text order and
# thread-vault placeholder numbering correct).
# Kept modest: the external pipeline is a scale-to-zero Cloud Run service, so a
# big fan-out triggers many cold-start instances and hurts more than it helps.
PII_SCAN_CONCURRENCY = 3

# Ingest scan ONLY: hard cap on how much of a file's text we route through the
# external pipeline for the card. A multi-MB file would otherwise become
# thousands of remote calls. Above this, only the first PII_SCAN_MAX_CHARS are
# scanned (the card may be incomplete for very large files); offsets stay valid
# because the card slices the full stored content, and detections live in the
# scanned prefix.
PII_SCAN_MAX_CHARS = 50000

# Ingest scan ONLY: retry a sub-chunk whose pipeline call fails (cold-start
# storm, timeout, transient connection error) instead of silently dropping its
# detections. Without this the card under-reports PII non-deterministically
# (same file -> different counts run to run).
PII_SCAN_PIECE_RETRIES = 3

# Chat-time (fail-closed) masking: retry a source-chunk POST whose pipeline call
# fails transiently (5xx, timeout, connection) before giving up. The pipeline's
# per-request vault snapshot occasionally trips its own DB command_timeout when
# many chunks/files hit it in one turn; a bounded retry of just the failed chunk
# rides that out automatically instead of surfacing a hard "PII inlet failed"
# error to the user (who then manually regenerates the WHOLE message). Still
# FAIL-CLOSED: after exhausting retries the block is raised and no unmasked text
# reaches the LLM. Retrying one chunk (~seconds) is far cheaper than a regenerate.
PII_MASK_POST_RETRIES = 3

# Verbose PII flow diagnostics to the server log. Set KEEPER_PII_DEBUG=1 (and
# restart the backend) to trace, per request: what the ingest scan identified
# (the CARD source) and what source text reaches the LLM (the chat path).
PII_DEBUG = os.environ.get("KEEPER_PII_DEBUG", "").lower() in ("1", "true", "yes", "on")

# Full-content dump: when KEEPER_PII_DEBUG_FILE points at a path, every request
# that carries file/RAG sources appends a human-readable block to that file
# showing, per chunk, the ORIGINAL text vs the MASKED text, plus the exact
# context_string that gets wrapped in the RAG template and sent to the LLM.
# This is what you `tail -f` to watch what the model actually reads — works
# identically for bypass-embedding and RAG (top-K) paths, since both funnel
# through apply_source_context_to_messages. Leave unset in production.
PII_DEBUG_FILE = os.environ.get("KEEPER_PII_DEBUG_FILE", "").strip()


def _pii_debug_dump(header: str, blocks: list, context_string: str):
    """Append a readable request block to KEEPER_PII_DEBUG_FILE. Best-effort:
    any I/O error is swallowed so diagnostics never break a chat request."""
    if not PII_DEBUG_FILE:
        return
    try:
        lines = [
            "=" * 80,
            header,
            "=" * 80,
        ]
        for i, (orig, masked, marker) in enumerate(blocks):
            changed = "CHANGED" if orig != masked else "unchanged"
            lines.append(
                f"\n--- chunk {i} [{marker}] "
                f"(orig {len(orig)} chars -> masked {len(masked)} chars, {changed}) ---"
            )
            lines.append("[ORIGINAL]")
            lines.append(orig)
            lines.append("[MASKED -> LLM]")
            lines.append(masked)
        lines.append("\n" + "-" * 40 + " FINAL context_string SENT TO LLM " + "-" * 40)
        lines.append(context_string)
        lines.append("\n")
        with open(PII_DEBUG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        log.warning("[PII-DEBUG] could not write dump file %s: %s", PII_DEBUG_FILE, e)

# User-visible message emitted on the tool/agentic path when masking is blocked.
PII_MASKING_BLOCK_MESSAGE = (
    "Request blocked: attachment/tool content could not be PII-masked "
    "(masking service unavailable). No unmasked data was sent to the model."
)


# Masked source documents, remembered per chat so an attachment is masked ONCE
# instead of on every turn. Measured on staging: a 167 460-char attachment cost
# 106.4s of masking, and `apply_source_context_to_messages` rebuilt and re-masked
# it for every later message in the same chat — a bare "thanks" waited two
# minutes. `metadata['masked_sources']` only ever lived for one request, so
# nothing carried the result forward.
#
# Reuse is sound because the input is genuinely identical turn to turn: an
# attachment carrying `context: 'full'` (retrieval/utils.py) hands back the whole
# stored document rather than a query-dependent TOP_K slice, and re-masking it
# calls `ThreadVault.get_placeholder`, an idempotent get-or-mint against the same
# thread vault — so the second run provably reproduces the first run's output.
# The cache returns that output without the round-trips.
#
# Keyed by chat_id because placeholders are minted per THREAD VAULT: chat A's
# `[PERSON_1]` is not chat B's, and serving one inside the other would hand the
# LLM placeholders the outlet cannot restore. Keyed by a hash of the document
# text (not by file id) so an edited or re-uploaded file misses instead of
# serving a stale body. Entries are only written when masking actually ran —
# see `mask_sources_for_llm`.
#
# Process-local and lost on restart. That is deliberate: a miss costs latency,
# never correctness, so there is no cache-invalidation surface to get wrong.
PII_SOURCE_CACHE_MAX_ENTRIES = 256

# Total masked text held across all entries. 20 MB is ~120 documents the size of
# the one measured above; past it the least-recently-used entries are dropped.
# A bound on entry COUNT alone would not do — one entry can be a megabyte.
PII_SOURCE_CACHE_MAX_CHARS = 20_000_000

_masked_source_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_masked_source_cache_chars = 0


def reset_masked_source_cache():
    """Drop every entry. For tests, which would otherwise be order-dependent."""
    global _masked_source_cache_chars
    _masked_source_cache.clear()
    _masked_source_cache_chars = 0


def _masked_source_cache_key(chat_id, file_id, doc):
    """Identify a document by its CONTENT, within one chat.

    `model_id` is deliberately absent. The masked text depends on the thread
    vault (chat_id) and the text itself; two models normally share the same PII
    filter, and if they did not, serving text masked under the other model's
    filter is over-masking — fail-safe, never a leak — whereas including it
    would throw the cache away every time a user switches model mid-chat, which
    is exactly when the saving matters.
    """
    return (
        chat_id,
        file_id,
        hashlib.sha256(doc.encode("utf-8", "surrogatepass")).hexdigest(),
    )


def _masked_source_cache_get(key):
    """Return `(masked_doc, detections)` or None, refreshing LRU position.

    The stored detections are copied out: callers tag them in place with
    fileId/fileName/docIdx, which would otherwise mutate the cached entry and
    accumulate on every hit.
    """
    hit = _masked_source_cache.get(key)
    if hit is None:
        return None
    _masked_source_cache.move_to_end(key)
    masked_doc, dets = hit
    return masked_doc, [dict(d) for d in dets]


def _masked_source_cache_put(key, masked_doc, detections):
    global _masked_source_cache_chars
    if not isinstance(masked_doc, str):
        return
    if key in _masked_source_cache:
        _masked_source_cache_chars -= len(_masked_source_cache[key][0])
        del _masked_source_cache[key]
    _masked_source_cache[key] = (masked_doc, [dict(d) for d in detections])
    _masked_source_cache_chars += len(masked_doc)
    while _masked_source_cache and (
        len(_masked_source_cache) > PII_SOURCE_CACHE_MAX_ENTRIES
        or _masked_source_cache_chars > PII_SOURCE_CACHE_MAX_CHARS
    ):
        _, (evicted, _dets) = _masked_source_cache.popitem(last=False)
        _masked_source_cache_chars -= len(evicted)


async def _resolve_pii_masking_decision(request, user, features):
    """Return `(policy_enforced, pii_expected)` for this request.

    Decision 2 — empty-filter semantics. Masking is "expected" unless explicitly
    disabled via `features.pii_masking=False` (default-on)... EXCEPT when team
    policy mandates masking, which beats the per-request flag exactly as it
    already does on the prompt path (`routers/pipelines.py`: `if policy_enforced
    and filter_id in PII_FILTER_IDS`). Without that, a user under a mandated
    policy could switch the toggle off and send an attachment's contents to the
    LLM unmasked while their prompt stayed masked — defeating the enforcement
    layer through the file path alone.

    `resolve_pii_masking_enforced` is memoized per request (on `request.state`,
    keyed by user id) and fails closed, so this costs ONE permission lookup per
    request even though every source chunk asks.
    """
    request_pii = features.get("pii_masking") if isinstance(features, dict) else None
    policy_enforced = await resolve_pii_masking_enforced(request, user)
    return policy_enforced, policy_enforced or request_pii is not False


async def _mask_text_via_pii_pipeline(
    request,
    text,
    *,
    session,
    chat_id,
    user,
    model_id,
    models,
    features,
    source_marker,
    post_retries: int = 1,
):
    """Route one source-text chunk through the external Presidio PII inlet and
    return the masked text. FAIL-CLOSED clone of ``process_pipeline_inlet_filter``:
    it mirrors ``get_sorted_filters()`` (no hardcoded PII filter id — the inlet
    has none) and the same per-filter valve injection, but RAISES
    ``PiiMaskingBlockedError`` on any failure (5xx / timeout / connection /
    malformed response) instead of returning unmasked text. Masking logic stays
    in the Presidio service; this only transports text + chat_id + a file-source
    marker.
    """
    if not isinstance(text, str) or text == "":
        return text, []

    if not chat_id:
        # Without a thread-vault key the placeholders cannot be keyed/restored.
        raise PiiMaskingBlockedError(
            "chat_id missing; refusing to mask source text without a thread vault key."
        )

    if models is None:
        models = request.app.state.MODELS

    # See `_resolve_pii_masking_decision` for the empty-filter / policy-override
    # semantics; `mask_sources_for_llm` asks the same question to decide whether
    # a masking result is safe to cache.
    features = features if isinstance(features, dict) else {}
    request_pii = features.get("pii_masking")
    policy_enforced, pii_expected = await _resolve_pii_masking_decision(
        request, user, features
    )

    if model_id not in models:
        # Cannot resolve the filter machinery for an unknown model -> leak risk.
        raise PiiMaskingBlockedError(
            f"model_id {model_id!r} not in model registry; cannot resolve PII inlet."
        )

    sorted_filters = get_sorted_filters(model_id, models)
    model = models[model_id]
    if "pipeline" in model:
        sorted_filters = [*sorted_filters, model]

    if not sorted_filters:
        if pii_expected:
            raise PiiMaskingBlockedError(
                "PII masking expected but no filter pipeline is configured for this model."
            )
        # Masking not requested and no machinery -> benign, nothing to do.
        return text, []

    # User explicitly disabled PII masking (features.pii_masking=False) -> pass
    # through without calling the pipeline.  The fail-closed guarantee only
    # applies when masking is expected; an explicit opt-out is a valid no-op.
    if not pii_expected:
        return text, []

    # user.settings -> ui.pipelines.valves (mirror process_pipeline_inlet_filter).
    user_settings = getattr(user, "settings", None)
    if isinstance(user_settings, dict):
        user_settings_dict = user_settings
    elif user_settings is not None:
        user_settings_dict = user_settings.model_dump()
    else:
        user_settings_dict = {}
    ui_settings = user_settings_dict.get("ui", {})
    if not isinstance(ui_settings, dict):
        ui_settings = {}
    pipelines_settings = ui_settings.get("pipelines", {})
    if not isinstance(pipelines_settings, dict):
        pipelines_settings = {}
    all_filter_valves = pipelines_settings.get("valves", {})
    if not isinstance(all_filter_valves, dict):
        all_filter_valves = {}

    base_user_dict = {
        "id": getattr(user, "id", None),
        "email": getattr(user, "email", None),
        "name": getattr(user, "name", None),
        "role": getattr(user, "role", None),
    }

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": text}],
        "metadata": {"chat_id": chat_id, "pii_source": source_marker},
        "features": features,
    }

    masked_count = 0
    for filter in sorted_filters:
        urlIdx = filter.get("urlIdx")
        try:
            urlIdx = int(urlIdx)
        except (TypeError, ValueError):
            continue

        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]
        if not key:
            continue

        filter_id = filter.get("id")
        per_filter_valves = all_filter_valves.get(filter_id, {})
        if not isinstance(per_filter_valves, dict):
            per_filter_valves = {}
        if isinstance(request_pii, bool):
            per_filter_valves = {**per_filter_valves, "pii_masking_enabled": request_pii}
        # The second half of the same guard, and the load-bearing one: the
        # pipeline decides solely from `UserValves.pii_masking_enabled` (it does
        # not read `features` at all — see its early return on opt-out), so
        # forcing `pii_expected` above without this would still hand it
        # "do not mask". Applied LAST on purpose: reversing these two blocks
        # gives the user's False the final word over the policy.
        if policy_enforced:
            per_filter_valves = {**per_filter_valves, "pii_masking_enabled": True}
        user_with_valves = {**base_user_dict, "valves": per_filter_valves}

        headers = {"Authorization": f"Bearer {key}"}
        request_data = {"user": user_with_valves, "body": payload}

        # Retry the POST on transient failure (5xx / timeout / connection). The
        # ONLY thing retried is this network round-trip; every deterministic guard
        # above (missing chat_id, unknown model, keyless filter) has already run
        # once and won't change on a retry. post_retries defaults to 1 (single
        # attempt) so the ingest path — which has its own outer retry — is
        # unchanged; the chat-time caller passes PII_MASK_POST_RETRIES.
        last_exc = None
        for attempt in range(max(1, post_retries)):
            try:
                async with session.post(
                    f"{url}/{filter['id']}/filter/inlet",
                    headers=headers,
                    json=request_data,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                # Count a fully successful POST+parse. An unchanged-text response
                # (filter found no PII) still counts — it is a valid pass, not a leak.
                masked_count += 1
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt + 1 < max(1, post_retries):
                    await asyncio.sleep(0.5 * (attempt + 1))
        if last_exc is not None:
            # FAIL-CLOSED after exhausting retries: never return unmasked text
            # (contrast pipelines.py:166, which fails open).
            raise PiiMaskingBlockedError(
                f"PII inlet failed for filter {filter.get('id')!r} "
                f"after {max(1, post_retries)} attempt(s): {last_exc}"
            ) from last_exc

    # C.1 keyless-filter guard: filters were present but every one was skipped
    # (no API key / invalid urlIdx) so nothing was actually masked. Fail closed
    # on the OUTCOME (zero successful masks) when masking was expected, rather
    # than leaking on filter *presence* alone.
    if pii_expected and masked_count == 0:
        raise PiiMaskingBlockedError(
            "PII masking expected but no applicable filter was usable "
            "(missing API key / invalid urlIdx); blocked before LLM."
        )

    try:
        masked = payload["messages"][0]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise PiiMaskingBlockedError(
            f"PII inlet returned a malformed payload: {e}"
        ) from e
    if not isinstance(masked, str):
        raise PiiMaskingBlockedError("PII inlet returned non-string content.")

    # B2: surface the pipeline's chunk-relative detection summary so the caller
    # can show file-sourced PII in the card. Whitelisted to {type,start,end} only
    # — NEVER value/original (trust boundary). The frontend reconstructs the value
    # locally from the original chunk it already holds via citations.
    detections = []
    response_md = payload.get("metadata") if isinstance(payload, dict) else None
    if isinstance(response_md, dict):
        for d in response_md.get("pii_detections_public") or []:
            if (
                isinstance(d, dict)
                and isinstance(d.get("type"), str)
                and isinstance(d.get("start"), int)
                and not isinstance(d.get("start"), bool)
                and isinstance(d.get("end"), int)
                and not isinstance(d.get("end"), bool)
            ):
                detections.append(
                    {"type": d["type"], "start": d["start"], "end": d["end"]}
                )
    return masked, detections


async def _mask_long_text_via_pii_pipeline(
    request, text, *, semaphore=None, on_piece=None, **kwargs
):
    """Mask one source ``document`` that may exceed the external pipeline's
    per-call budget (TRAU-513). Splits the document into sub-chunks under the
    cap (boundary-aware — never cutting an entity), masks them CONCURRENTLY via
    ``_mask_text_via_pii_pipeline``, then:

      * reassembles the masked sub-chunks in document order, and
      * merges detections with DOCUMENT-relative offsets (so the frontend keeps
        slicing values out of the original chunk it already holds), de-duped per
        document by ``(type, start, end)``.

    Concurrency mirrors the prompt path (TRAU-543): the pipeline serializes NER
    on one thread per instance, so the win here is overlapping one chunk's
    network / vault / parse work with another's detection, not extra CPU.
    Sequential masking cost a document ``chunks x ~7.5s`` inside the chat
    request while the identical text pasted into the prompt was masked
    concurrently — the same work, minutes apart, decided only by how the user
    supplied it.

    ``semaphore`` is supplied by the caller so ONE bound covers every document
    in the request; creating it per document would let N attachments multiply
    the fan-out N times over. Concurrency is safe: ``ThreadVault.get_placeholder``
    is an atomic get-or-mint, so racing chunks carrying the same value receive
    the same placeholder.

    FAIL-CLOSED is unchanged: ``asyncio.gather`` without ``return_exceptions``
    propagates the first ``PiiMaskingBlockedError`` and abandons the rest, so a
    partially-masked document can never be assembled.

    For a document already under the cap this is exactly one masking call with
    identical behaviour to the pre-fix path (no extra POST, same offsets).
    """
    if not isinstance(text, str) or text == "":
        return text, []

    pieces = split_text_for_pii(text)
    def _note_piece():
        """Report one finished sub-chunk, never letting the report break the
        masking. `asyncio.gather` below runs without `return_exceptions`, so an
        exception escaping here would abandon a half-masked document — a
        progress bar must not be able to do that."""
        if on_piece is None:
            return
        try:
            on_piece()
        except Exception as e:  # noqa: BLE001 — diagnostics must not break masking
            log.debug(f'[pii_chunking] progress callback failed: {e}')

    if len(pieces) == 1:
        # Fast path: short doc -> unchanged single-call behaviour.
        result = await _mask_text_via_pii_pipeline(request, text, **kwargs)
        _note_piece()
        return result

    sem = semaphore if semaphore is not None else asyncio.Semaphore(PII_INLET_CONCURRENCY)
    masked_pieces: dict[int, str] = {}
    piece_detections: dict[int, list] = {}

    async def _mask_piece(index, piece_start, piece):
        async with sem:
            masked_piece, piece_dets = await _mask_text_via_pii_pipeline(
                request, piece, **kwargs
            )
        masked_pieces[index] = masked_piece
        piece_detections[index] = [
            {
                "type": d["type"],
                "start": d["start"] + piece_start,
                "end": d["end"] + piece_start,
            }
            for d in piece_dets
        ]
        _note_piece()

    await asyncio.gather(
        *(_mask_piece(i, start, piece) for i, (start, piece) in enumerate(pieces))
    )

    # Reassembly indexes `pieces`, not whatever landed in `masked_pieces`:
    # deriving the range from the results would silently TRUNCATE the document
    # to its first k sub-chunks if this ever tolerated partial failures.
    detections = []
    seen = set()
    for i in range(len(pieces)):
        for doc_det in piece_detections[i]:
            key = (doc_det["type"], doc_det["start"], doc_det["end"])
            if key in seen:
                continue
            seen.add(key)
            detections.append(doc_det)

    return "".join(masked_pieces[i] for i in range(len(pieces))), detections


def _resolve_pii_scan_model_id(models):
    """Pick any model the PII inlet filter applies to (the filter is global,
    pipelines=['*']). Returns the model id, or None if no filter is usable —
    in which case the ingest scan is skipped (best-effort)."""
    if not isinstance(models, dict):
        return None
    for model_id, model in models.items():
        if isinstance(model, dict) and "pipeline" in model:
            continue  # skip the filter-pipeline pseudo-models themselves
        if get_sorted_filters(model_id, models):
            return model_id
    return None


def ingest_scan_is_truncated(content) -> bool:
    """Whether the ingest scan will look at only a PREFIX of ``content``.

    One rule, two callers: the cap applied below, and
    ``_store_ingest_pii_detections``, which records the answer on the file so
    the card can tell that it is showing a partial picture. Measured on
    staging, a 167 460-char document scanned to 50 000 produced 28 card
    detections where the full text holds 163 — and because the frontend treats
    a `completed` scan as authoritative it also SUPPRESSED the send-time
    detections that would have filled the gap. Keeping the two callers on the
    same predicate is what stops that discrepancy from going silent again.
    """
    return isinstance(content, str) and len(content) > PII_SCAN_MAX_CHARS


async def scan_file_content_for_pii(
    request, content, *, file_id, user, models=None, features=None
):
    """BEST-EFFORT full-file PII detection for the PII card (NOT the fail-closed
    security boundary). Scans the entire extracted ``content`` through the
    external Presidio inlet (sub-chunked, no token-cap truncation) and returns
    span-only file-relative detections ``[{type,start,end}]``. NEVER raises and
    NEVER blocks ingest: returns ``[]`` on any problem. The masked text and the
    synthetic per-file vault entry are discarded; only detections are kept.
    """
    if not isinstance(content, str) or content == "":
        return []
    if models is None:
        models = request.app.state.MODELS
    model_id = _resolve_pii_scan_model_id(models)
    if model_id is None:
        return []  # no PII filter configured -> nothing to scan

    feats = features if isinstance(features, dict) else {"pii_masking": True}
    source_marker = {"type": "file", "file_id": file_id}
    # Hard cap the scanned volume so a huge file can't become thousands of remote
    # calls. The card slices the FULL stored content, so prefix offsets stay valid.
    if ingest_scan_is_truncated(content):
        log.info(
            "ingest PII scan: capping file %s content %d -> %d chars",
            file_id,
            len(content),
            PII_SCAN_MAX_CHARS,
        )
        content = content[:PII_SCAN_MAX_CHARS]
    # Sub-chunk the whole file (newline-bounded, no token-cap truncation) and mask
    # each piece. Unlike the chat-time path we DISCARD the masked text and keep only
    # the detection spans, so the pieces are independent -> we run them CONCURRENTLY
    # (bounded by PII_SCAN_CONCURRENCY). For a large document this turns ~100 serial
    # round-trips into a handful of parallel batches (minutes -> tens of seconds).
    pieces = split_text_for_pii(content)
    # total scales with the (bounded-concurrency) number of sub-chunk POSTs.
    timeout = aiohttp.ClientTimeout(
        sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ, connect=5, total=120
    )
    semaphore = asyncio.Semaphore(PII_SCAN_CONCURRENCY)
    _t0 = time.time()
    _stats = {"ok": 0, "failed": 0}

    async def _scan_piece(session, piece_start, piece):
        last_exc = None
        for attempt in range(PII_SCAN_PIECE_RETRIES):
            try:
                async with semaphore:
                    _masked, piece_dets = await _mask_text_via_pii_pipeline(
                        request,
                        piece,
                        session=session,
                        chat_id=f"file-{file_id}",  # synthetic vault key; result ignored
                        user=user,
                        model_id=model_id,
                        models=models,
                        features=feats,
                        source_marker=source_marker,
                    )
                _stats["ok"] += 1
                # Rebase chunk-relative offsets to file-relative.
                return [
                    {"type": d["type"], "start": d["start"] + piece_start, "end": d["end"] + piece_start}
                    for d in piece_dets
                ]
            except Exception as e:
                # Transient (cold start / timeout / connection). Back off and retry
                # — the semaphore is released during the sleep so peers proceed.
                last_exc = e
                if attempt + 1 < PII_SCAN_PIECE_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
        log.warning(
            "ingest PII scan chunk failed after %d attempts for file %s: %s",
            PII_SCAN_PIECE_RETRIES,
            file_id,
            last_exc,
        )
        _stats["failed"] += 1
        return []  # drop only this chunk after exhausting retries; keep the rest

    try:
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            results = await asyncio.gather(
                *(_scan_piece(session, ps, p) for ps, p in pieces),
                return_exceptions=True,  # one bad chunk must not drop the rest
            )
    except Exception as e:  # best-effort: log and degrade, never block ingest
        log.warning(
            "ingest PII scan failed for file %s: %s", file_id, e, exc_info=True
        )
        return []

    # Merge + dedup per DOCUMENT by (type, start, end); skip chunks that errored.
    seen = set()
    detections = []
    for chunk in results:
        if isinstance(chunk, BaseException):
            log.warning(
                "ingest PII scan chunk failed for file %s: %s", file_id, chunk
            )
            continue
        for d in chunk:
            key = (d["type"], d["start"], d["end"])
            if key in seen:
                continue
            seen.add(key)
            detections.append(d)
    if PII_DEBUG:
        log.info(
            "[PII-DEBUG][INGEST] file=%s chars=%d chunks=%d ok=%d failed=%d "
            "detections=%d elapsed=%.1fs  <- this is what the PII CARD shows",
            file_id,
            len(content),
            len(pieces),
            _stats["ok"],
            _stats["failed"],
            len(detections),
            time.time() - _t0,
        )
    return detections


def get_source_context(sources: list, source_ids: dict = None, include_content: bool = True) -> str:
    """
    Build <source> tag context string from citation sources.
    """
    context_string = ''
    if source_ids is None:
        source_ids = {}
    for source in sources:
        for doc, meta in zip(source.get('document', []), source.get('metadata', [])):
            source_id = meta.get('source') or source.get('source', {}).get('id') or 'N/A'
            if source_id not in source_ids:
                source_ids[source_id] = len(source_ids) + 1
            src_name = source.get('source', {}).get('name')
            src_type = source.get('source', {}).get('type')
            src_rid = source.get('source', {}).get('id')
            body = doc if include_content else ''
            context_string += (
                f'<source id="{source_ids[source_id]}"'
                + (f' name="{src_name}"' if src_name else '')
                + (f' resource-type="{src_type}"' if src_type else '')
                + (f' resource-id="{src_rid}"' if src_rid else '')
                + f'>{body}</source>\n'
            )
    return context_string


# Strong references to in-flight progress-event tasks. `asyncio.create_task`
# only holds a WEAK reference to the task it schedules — with nothing else
# referencing it, the task can be garbage-collected before it runs (ruff
# RUF006). This set is that reference; `_pii_progress_task_done` below
# removes each task once it finishes.
_pii_progress_tasks: set = set()


def _pii_progress_task_done(task):
    _pii_progress_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    # A raised exception here must never surface as an unretrieved-task-
    # exception warning — a progress event is diagnostics, not the masking
    # path it reports on.
    if exc is not None:
        log.debug(f'[pii_chunking] progress event task failed: {exc}')


def _should_emit_pii_progress(done, total):
    """Whether a PII-masking progress update is worth persisting.

    Each status event drives a non-atomic read-modify-write of the WHOLE chat
    row (`Chats.add_message_status_to_chat_by_id_and_message_id` ->
    `update_chat_by_id`, no optimistic-concurrency check): fetch the chat,
    append to `statusHistory`, write the entire document back. Emitting once
    per chunk on a large paste (dozens to ~100 chunks, up to
    `PII_INLET_CONCURRENCY` of those in flight concurrently) turns into that
    many concurrent whole-row rewrites, which can clobber each other's
    appended entries. Throttle to roughly twenty events total instead: always
    the first completion and the terminal one (so the bar always starts and
    always reaches 100%), otherwise only every ~5% of the work.

    Twenty rather than ten because raising `PII_INLET_TOTAL_BUDGET_S`
    multiplied the largest admissible chunk count by about five. At ten events
    a ~190 000-character paste would update once every ~80s of an eight-minute
    wait — indistinguishable from a hung request. Not raised further because
    the cost here is concurrent whole-row rewrites, not websocket traffic, and
    the shimmer in `StatusItem.svelte` already carries liveness between
    updates; the number does not have to.
    """
    if done <= 1 or done >= total:
        return True
    step = max(1, total // 20)
    return done % step == 0


def _pii_progress_emitter(event_emitter):
    """Adapt a synchronous `on_progress(done, total)` callback to the async
    status-event channel.

    Ported from the prompt path (`fix/TRAU-543`) so an attachment reports the
    same signal, through the same renderer, as a large paste: masking a
    document costs the same minutes and showed nothing at all. Two producers
    now call it — the inlet's chunked prompt masking there, and
    `mask_sources_for_llm` here.

    The callback fires from inside `asyncio.gather`, so it cannot await; the
    event is scheduled instead. Best-effort by construction — a progress event
    that fails to emit must never affect masking, which is a security path:
    the producer calls `on_progress` from inside `_mask_piece`'s retry `try`,
    so a synchronous exception escaping here would be caught as a transient
    chunk failure and cause a spurious re-POST of an already-masked chunk.
    """

    def on_progress(done, total):
        try:
            # Inside the try on purpose: `done >= total` here and inside the
            # throttle helper is a comparison on whatever the producer hands
            # us. If a future change ever passes a non-comparable `total`
            # (e.g. None), that must be swallowed too — not just the
            # scheduling below it — or the propagating TypeError lands in
            # `_mask_piece`'s retry `try` and triggers a spurious re-POST of
            # an already-masked chunk.
            if not _should_emit_pii_progress(done, total):
                return
            task = asyncio.create_task(
                event_emitter(
                    {
                        'type': 'status',
                        'data': {
                            'action': 'pii_masking',
                            'description': 'Masking sensitive data',
                            'count': done,
                            'total': total,
                            'done': done >= total,
                        },
                    }
                )
            )
            _pii_progress_tasks.add(task)
            task.add_done_callback(_pii_progress_task_done)
        except Exception as e:  # noqa: BLE001 — diagnostics must not break masking
            log.debug(f'[pii_chunking] could not emit progress: {e}')

    return on_progress


async def mask_sources_for_llm(
    request: Request,
    sources: list,
    chat_id: Optional[str] = None,
    user=None,
    model_id: Optional[str] = None,
    models=None,
    features=None,
    on_progress=None,
) -> tuple[list, list[dict]]:
    """
    Task 3.6 (file/tool-attachment PII): route every source ``document`` chunk
    through the external Presidio PII inlet (fail-closed) and return a copy of
    ``sources`` whose documents are masked, plus the span-only detections.

    The ORIGINAL ``sources`` list is left untouched — it is what gets emitted to
    the frontend as citations, and B2 reconstructs each masked value client-side
    from that original text (no value ever crosses this boundary). Only the copy
    returned here may reach the LLM.

    ``source["metadata"]`` is carried over 1:1 so citation mapping is preserved.
    Any masking failure raises ``PiiMaskingBlockedError`` (never leaks unmasked
    text).
    """
    masked_sources = []
    detections: list[dict] = []
    _t0 = time.time()
    _dbg_docs = 0
    _dbg_orig_chars = 0
    _dbg_blocks = []  # [(orig, masked, marker)] for the full-content dump file

    # Whether a result from this request is safe to remember. Gated on masking
    # actually being expected, which is LOAD-BEARING and not an optimisation:
    # when the user has opted out (and no policy mandates otherwise)
    # `_mask_text_via_pii_pipeline` returns the text UNCHANGED without calling
    # the pipeline. Caching that pass-through would let a later turn — with
    # masking switched back ON — be served the raw document, sending PII
    # straight to the LLM. Asking the same question the masking call asks keeps
    # the two decisions from drifting apart.
    _, _pii_expected = await _resolve_pii_masking_decision(request, user, features)
    cache_enabled = bool(chat_id) and _pii_expected
    _dbg_cached_docs = 0

    # Resolve every cache lookup up front, before both the budget guard and the
    # progress total, so each of them prices the work actually LEFT TO DO.
    #
    # For progress: counting DOCUMENTS would leave the bar at 0/1 for a whole
    # two-minute wait (one attachment is one document but ~93 POSTs), and
    # counting cached ones would fill a bar for work that never happens.
    #
    # For the budget: it used to sum every source character, cached or not, so a
    # turn could be refused as "too large" for work it was about to skip — a
    # cached 200 000-char attachment plus a new 150 000-char one is 350 000 on
    # paper and past the cap, while the real cost is the 150 000. That also
    # contradicted the deadline below, which bounds real work.
    #
    # Splitting here and again inside the masking call is pure string work,
    # microseconds against a network round trip.
    resolved: dict = {}
    total_pieces = 0
    uncached_chars = 0
    for _s_idx, _source in enumerate(sources):
        _docs = _source.get('document', []) or []
        _metas = _source.get('metadata', []) or []
        for _d_idx, (_doc, _meta) in enumerate(zip(_docs, _metas)):
            _meta = _meta if isinstance(_meta, dict) else {}
            _key = (
                _masked_source_cache_key(chat_id, _meta.get('file_id'), _doc)
                if cache_enabled and isinstance(_doc, str)
                else None
            )
            _hit = _masked_source_cache_get(_key) if _key is not None else None
            resolved[(_s_idx, _d_idx)] = (_key, _hit)
            if _hit is None and isinstance(_doc, str) and _doc:
                total_pieces += len(split_text_for_pii(_doc))
                uncached_chars += len(_doc)

    # Budget guard, before any POST. Charged across EVERY source, not per
    # source: wall clock is a property of the request, and a per-source cap let
    # N attachments multiply the real cost N times over. Past the budget the
    # honest answer is an immediate refusal — the alternative is a request that
    # masks for minutes and is refused anyway, which is precisely the
    # wait-then-refuse the prompt path removed.
    #
    # `estimated_masking_seconds(0, chars)` prices everything as chunked work
    # (there is no skeleton call on this path), so the admissible total is
    # exactly `max_maskable_chars()` — the same number the prompt path allows,
    # so the same document costs the same whether pasted or attached.
    if estimated_masking_seconds(0, uncached_chars) > PII_INLET_TOTAL_BUDGET_S:
        raise PiiMaskingBlockedError(
            'These attachments are too large to mask safely. Remove one, or shorten them.'
        )

    # ONE bound for the whole request. Created here rather than per document so
    # five attachments cannot open five times the fan-out at a pipeline that
    # serializes NER on a single thread anyway.
    semaphore = asyncio.Semaphore(PII_INLET_CONCURRENCY)

    _done_pieces = 0

    def _piece_done():
        nonlocal _done_pieces
        _done_pieces += 1
        if on_progress is not None and total_pieces:
            on_progress(_done_pieces, total_pieces)

    # C.2: open ONE aiohttp session for every chunk-masking call this request,
    # instead of one session per chunk. Same timeout/SSL as the existing inlet.
    timeout = aiohttp.ClientTimeout(sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ, connect=5, total=30)

    async def _mask_every_source():
        nonlocal _dbg_docs, _dbg_orig_chars, _dbg_cached_docs
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            for src_idx, source in enumerate(sources):
                docs = source.get('document', [])
                metas = source.get('metadata', [])
                src_meta = source.get('source', {}) or {}

                masked_docs = []
                for doc_idx, (doc, meta) in enumerate(zip(docs, metas)):
                    meta = meta if isinstance(meta, dict) else {}
                    # File-source marker so the vault records this PII as file/tool-sourced.
                    source_marker = {
                        'type': src_meta.get('type'),
                        'name': src_meta.get('name'),
                        'file_id': meta.get('file_id'),
                        'note_id': meta.get('note_id'),
                    }
                    # Mask the chunk via the external Presidio inlet BEFORE the wrap.
                    # _mask_long_text_via_pii_pipeline sub-chunks any document longer
                    # than the pipeline's token cap so its tail is never silently
                    # truncated (TRAU-513). Short docs take a single masking call.
                    _orig_doc = doc
                    cache_key, cached = resolved.get((src_idx, doc_idx), (None, None))
                    if cached is not None:
                        doc, chunk_detections = cached
                        _dbg_cached_docs += 1
                    else:
                        doc, chunk_detections = await _mask_long_text_via_pii_pipeline(
                            request,
                            doc,
                            semaphore=semaphore,
                            session=session,
                            chat_id=chat_id,
                            user=user,
                            model_id=model_id,
                            models=models if models is not None else request.app.state.MODELS,
                            features=features,
                            source_marker=source_marker,
                            post_retries=PII_MASK_POST_RETRIES,
                            on_piece=_piece_done,
                        )
                        # Only reached when the call RETURNED. A failure raises
                        # PiiMaskingBlockedError straight past here, so a run that
                        # blew up never leaves an entry to be mistaken for a success.
                        if cache_key is not None:
                            _masked_source_cache_put(cache_key, doc, chunk_detections)
                    masked_docs.append(doc)

                    if PII_DEBUG:
                        _dbg_docs += 1
                        _dbg_orig_chars += len(_orig_doc) if isinstance(_orig_doc, str) else 0
                    if PII_DEBUG_FILE:
                        _dbg_blocks.append(
                            (
                                _orig_doc if isinstance(_orig_doc, str) else str(_orig_doc),
                                doc if isinstance(doc, str) else str(doc),
                                f"{src_meta.get('name') or src_meta.get('type') or 'source'} doc={doc_idx}",
                            )
                        )

                    # B2: tag each chunk-relative detection with the file + chunk it
                    # came from. The frontend slices the value out of the ORIGINAL
                    # chunk it already holds (citations) — no value travels here.
                    file_id = meta.get('file_id') or src_meta.get('id')
                    file_name = src_meta.get('name')
                    for d in chunk_detections:
                        detections.append(
                            {
                                'type': d['type'],
                                'start': d['start'],
                                'end': d['end'],
                                'fileId': file_id,
                                'fileName': file_name,
                                'docIdx': doc_idx,
                            }
                        )

                masked_source = {**source, 'document': masked_docs}
                masked_sources.append(masked_source)

    # THE deadline. The check at the top of this function is a forecast — it
    # prices the work from a modelled throughput and refuses before the first
    # POST. Nothing bounded the actual run, so whenever the pipeline was slower
    # than the model (cold start, contention, a degraded revision) the request
    # kept going: per-POST socket timeouts x retries x every sub-chunk, with the
    # chat request held open throughout. This turns that into a bounded wait and
    # a refusal the user can act on.
    #
    # Fail-closed by construction: `wait_for` cancels the gather, so no
    # partially-masked document is ever assembled, and the cache write sits
    # AFTER the masking call inside the cancelled task — nothing half-finished
    # is left behind for the next turn to serve.
    try:
        await asyncio.wait_for(_mask_every_source(), timeout=PII_INLET_TOTAL_BUDGET_S)
    except asyncio.TimeoutError:
        raise PiiMaskingBlockedError(
            'Masking the attached files did not finish in time, so nothing was sent. '
            'Try again, or use a smaller document.'
        )

    if PII_DEBUG:
        log.info(
            '[PII-DEBUG][SOURCES->LLM] full_context=%s sources=%d docs=%d '
            'cached_docs=%d orig_chars=%d masked_entities=%d elapsed=%.1fs '
            ' <- this is what the LLM receives, and what it cost',
            getattr(request.app.state.config, 'RAG_FULL_CONTEXT', None),
            len(sources),
            _dbg_docs,
            _dbg_cached_docs,
            _dbg_orig_chars,
            len(detections),
            time.time() - _t0,
        )
    if PII_DEBUG_FILE:
        _pii_debug_dump(
            header=(
                f'[SOURCES->LLM] chat_id={chat_id} model={model_id} '
                f"bypass={getattr(request.app.state.config, 'BYPASS_EMBEDDING_AND_RETRIEVAL', None)} "
                f"full_context={getattr(request.app.state.config, 'RAG_FULL_CONTEXT', None)} "
                f'sources={len(sources)} chunks={len(_dbg_blocks)}'
            ),
            blocks=_dbg_blocks,
            context_string=get_source_context(masked_sources).strip(),
        )

    return masked_sources, detections


async def apply_source_context_to_messages(
    request: Request,
    messages: list,
    sources: list,
    user_message: str,
    include_content: bool = True,
    chat_id: Optional[str] = None,
    user=None,
    model_id: Optional[str] = None,
    models=None,
    features=None,
    on_progress=None,
) -> tuple[list, list[dict], list]:
    """
    Build source context from citation sources and apply to messages.
    Uses RAG template to format context for model consumption.

    When include_content is False, emit <source> tags with id/name but no
    document body — useful when the content is already present elsewhere
    (e.g. in a tool result message) and only citation markers are needed.

    Task 3.6 (file/tool-attachment PII): documents are PII-masked (fail-closed)
    before being wrapped in ``<source>`` tags. Returns
    ``(messages, pii_detections, masked_sources)`` — the caller keeps
    ``masked_sources`` so any later re-render of the same sources (tool/agentic
    path) reuses the masked text instead of the original.
    """
    if not sources or not user_message:
        return messages, [], []

    # Masking is skipped entirely when the context body is not emitted — no
    # document text reaches the LLM through this call.
    if include_content:
        masked_sources, detections = await mask_sources_for_llm(
            request,
            sources,
            chat_id=chat_id,
            user=user,
            model_id=model_id,
            models=models,
            features=features,
            on_progress=on_progress,
        )
    else:
        masked_sources, detections = sources, []

    context = get_source_context(masked_sources, include_content=include_content)

    context = context.strip()
    if not context:
        return messages, detections, masked_sources

    if RAG_SYSTEM_CONTEXT:
        messages = add_or_update_system_message(
            await rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=True,
        )
    else:
        messages = add_or_update_user_message(
            await rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=False,
        )

    return messages, detections, masked_sources


async def process_tool_result(
    request,
    tool_function_name,
    tool_result,
    tool_type,
    direct_tool=False,
    metadata=None,
    user=None,
):
    tool_result_embeds = []
    EXTERNAL_TOOL_TYPES = ('external', 'action', 'terminal')

    # Support (HTMLResponse, result_context) tuples: the optional second
    # element lets tool authors provide the LLM with actionable context
    # about the generated embed instead of the generic fallback message.
    result_context = None
    if isinstance(tool_result, tuple) and len(tool_result) == 2 and isinstance(tool_result[0], HTMLResponse):
        tool_result, result_context = tool_result

    if isinstance(tool_result, HTMLResponse):
        content_disposition = tool_result.headers.get('Content-Disposition', '')
        if 'inline' in content_disposition:
            content = tool_result.body.decode('utf-8', 'replace')
            tool_result_embeds.append(content)

            if 200 <= tool_result.status_code < 300:
                if result_context is not None and isinstance(result_context, (str, dict, list)):
                    tool_result = result_context
                else:
                    tool_result = {
                        'status': 'success',
                        'code': 'ui_component',
                        'message': f'{tool_function_name}: Embedded UI result is active and visible to the user.',
                    }
            elif 400 <= tool_result.status_code < 500:
                tool_result = {
                    'status': 'error',
                    'code': 'ui_component',
                    'message': f'{tool_function_name}: Client error {tool_result.status_code} from embedded UI result.',
                }
            elif 500 <= tool_result.status_code < 600:
                tool_result = {
                    'status': 'error',
                    'code': 'ui_component',
                    'message': f'{tool_function_name}: Server error {tool_result.status_code} from embedded UI result.',
                }
            else:
                tool_result = {
                    'status': 'error',
                    'code': 'ui_component',
                    'message': f'{tool_function_name}: Unexpected status code {tool_result.status_code} from embedded UI result.',
                }
        else:
            tool_result = tool_result.body.decode('utf-8', 'replace')

    elif (tool_type in EXTERNAL_TOOL_TYPES and isinstance(tool_result, tuple)) or (
        direct_tool and isinstance(tool_result, list) and len(tool_result) == 2
    ):
        tool_result, tool_response_headers = tool_result

        try:
            if not isinstance(tool_response_headers, dict):
                tool_response_headers = dict(tool_response_headers)
        except Exception as e:
            tool_response_headers = {}
            log.debug(e)

        if tool_response_headers and isinstance(tool_response_headers, dict):
            content_disposition = tool_response_headers.get(
                'Content-Disposition',
                tool_response_headers.get('content-disposition', ''),
            )

            if 'inline' in content_disposition:
                content_type = tool_response_headers.get(
                    'Content-Type',
                    tool_response_headers.get('content-type', ''),
                )
                location = tool_response_headers.get(
                    'Location',
                    tool_response_headers.get('location', ''),
                )

                if 'text/html' in content_type:
                    # Support (html_content, result_context) nested tuple
                    result_context = None
                    html_content = tool_result
                    if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
                        html_content, result_context = tool_result

                    # Display as iframe embed
                    tool_result_embeds.append(html_content)
                    if result_context is not None and isinstance(result_context, (str, dict, list)):
                        tool_result = result_context
                    else:
                        tool_result = {
                            'status': 'success',
                            'code': 'ui_component',
                            'message': f'{tool_function_name}: Embedded UI result is active and visible to the user.',
                        }
                elif location:
                    # Support (html_content, result_context) nested tuple for location embeds
                    result_context = None
                    if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
                        _, result_context = tool_result

                    tool_result_embeds.append(location)
                    if result_context is not None and isinstance(result_context, (str, dict, list)):
                        tool_result = result_context
                    else:
                        tool_result = {
                            'status': 'success',
                            'code': 'ui_component',
                            'message': f'{tool_function_name}: Embedded UI result is active and visible to the user.',
                        }

    tool_result_files = []

    # Detect base64 image data URIs from tool results (e.g. binary image
    # responses from execute_tool_server).  Move the data URI to
    # tool_result_files and replace tool_result with a text summary.
    if isinstance(tool_result, str) and tool_result.startswith('data:image/'):
        tool_result_files.append({'type': 'image', 'url': tool_result})
        tool_result = f'{tool_function_name}: Image file read successfully.'

    if isinstance(tool_result, list):
        if tool_type == 'mcp':  # MCP
            tool_response = []
            for item in tool_result:
                if isinstance(item, dict):
                    if item.get('type') == 'text':
                        text = item.get('text', '')
                        if isinstance(text, str):
                            try:
                                text = JSONCodec.loads(text)
                            except json.JSONDecodeError:
                                pass
                        tool_response.append(text)
                    elif item.get('type') in ['image', 'audio']:
                        file_url = await get_file_url_from_base64(
                            request,
                            f'data:{item.get("mimeType")};base64,{item.get("data", item.get("blob", ""))}',
                            {
                                'chat_id': metadata.get('chat_id', None),
                                'message_id': metadata.get('message_id', None),
                                'session_id': metadata.get('session_id', None),
                                'result': item,
                            },
                            user,
                        )

                        tool_result_files.append(
                            {
                                'type': item.get('type', 'data'),
                                'url': file_url,
                            }
                        )
                    elif item.get('type') == 'resource':
                        resource = item.get('resource', {})
                        text = resource.get('text', '')
                        if isinstance(text, str) and text:
                            try:
                                text = JSONCodec.loads(text)
                            except json.JSONDecodeError:
                                pass
                            tool_response.append(text)
                        elif resource.get('blob'):
                            resource_mime_type = resource.get('mimeType') or 'application/octet-stream'
                            resource_blob = resource.get('blob', '')
                            if resource_mime_type.startswith('image/'):
                                tool_result_files.append(
                                    {
                                        'type': 'image',
                                        'url': f'data:{resource_mime_type};base64,{resource_blob}',
                                    }
                                )
                            else:
                                resource_uri = resource.get('uri', 'resource')
                                tool_response.append(
                                    f'[Resource: {resource_uri}] (binary data, mimeType: {resource_mime_type})'
                                )
                        elif resource.get('uri'):
                            tool_response.append(resource.get('uri'))
            tool_result = tool_response[0] if len(tool_response) == 1 else tool_response
        else:  # OpenAPI
            for item in tool_result:
                if isinstance(item, str) and item.startswith('data:'):
                    tool_result_files.append(
                        {
                            'type': 'data',
                            'content': item,
                        }
                    )
                    tool_result.remove(item)

    if isinstance(tool_result, list):
        tool_result = {'results': tool_result}

    if isinstance(tool_result, dict) or isinstance(tool_result, list):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

    # Safety: ensure tool_result is always a string (or None) to prevent
    # downstream TypeError when concatenating (e.g. if an upstream callable
    # returned a tuple that was not unpacked by the branches above).
    if tool_result is not None and not isinstance(tool_result, str):
        if isinstance(tool_result, tuple):
            # execute_tool_server returns (data, headers); unpack the data part
            tool_result = json.dumps(tool_result[0], indent=2, ensure_ascii=False) if len(tool_result) > 0 else ''
        else:
            tool_result = str(tool_result)

    return tool_result, tool_result_files, tool_result_embeds


async def terminal_event_handler(
    tool_function_name: str,
    tool_function_params: dict,
    tool_result,
    event_emitter,
):
    """Emit terminal:* events for Open Terminal tools.

    - display_file  → emits 'terminal:display_file' to open the file preview.
    - write_file / replace_file_content → emits 'terminal:write_file' to refresh.
    - run_command → emits 'terminal:run_command' with cwd to refresh if relevant.
    """
    if not event_emitter:
        return

    if tool_function_name == 'display_file':
        path = tool_function_params.get('path', '')
        if not path:
            return
        # Only emit if the file actually exists
        parsed = tool_result
        if isinstance(parsed, str):
            try:
                parsed = JSONCodec.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(parsed, dict) and parsed.get('exists') is False:
            return

        await event_emitter(
            {
                'type': f'terminal:{tool_function_name}',
                'data': {'path': path},
            }
        )
    elif tool_function_name in ('write_file', 'replace_file_content'):
        path = tool_function_params.get('path', '')
        if not path:
            return
        await event_emitter(
            {
                'type': f'terminal:{tool_function_name}',
                'data': {'path': path},
            }
        )
    elif tool_function_name == 'run_command':
        await event_emitter(
            {
                'type': 'terminal:run_command',
                'data': {},
            }
        )


async def chat_completion_tools_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel, models, tools
) -> tuple[dict, dict]:
    async def get_content_from_response(response) -> Optional[str]:
        content = None
        if hasattr(response, 'body_iterator'):
            async for chunk in response.body_iterator:
                data = JSONCodec.loads(chunk.decode('utf-8', 'replace'))
                content = data['choices'][0]['message']['content']

            # Cleanup any remaining background tasks if necessary
            if response.background is not None:
                await response.background()
        else:
            content = response['choices'][0]['message']['content']
        return content

    def get_tools_function_calling_payload(messages, task_model_id, content):
        user_message = get_last_user_message(messages)

        if user_message and messages and messages[-1]['role'] == 'user':
            # Remove the last user message to avoid duplication
            messages = messages[:-1]

        recent_messages = messages[-4:] if len(messages) > 4 else messages
        chat_history = '\n'.join(
            f'{message["role"].upper()}: """{get_content_from_message(message)}"""' for message in recent_messages
        )

        prompt = f'History:\n{chat_history}\nQuery: {user_message}' if chat_history else f'Query: {user_message}'

        return {
            'model': task_model_id,
            'messages': [
                {'role': 'system', 'content': content},
                {'role': 'user', 'content': prompt},
            ],
            'stream': False,
            'metadata': {'task': str(TASKS.FUNCTION_CALLING)},
        }

    event_caller = extra_params['__event_call__']
    event_emitter = extra_params['__event_emitter__']
    metadata = extra_params['__metadata__']

    task_model_id = get_task_model_id(
        body['model'],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    skip_files = False
    sources = []

    specs = [tool['spec'] for tool in tools.values()]
    tools_specs = json.dumps(specs, ensure_ascii=False)

    tools_prompt_template = request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    if tools_prompt_template != '':
        template = tools_prompt_template
    else:
        template = DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    tools_function_calling_prompt = tools_function_calling_generation_template(template, tools_specs)
    payload = get_tools_function_calling_payload(body['messages'], task_model_id, tools_function_calling_prompt)

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        log.debug(f'{response=}')
        content = await get_content_from_response(response)
        log.debug(f'{content=}')

        if not content:
            return body, {}

        try:
            content = content[content.find('{') : content.rfind('}') + 1]
            if not content:
                raise Exception('No JSON object found in the response')

            result = JSONCodec.loads(content)

            async def tool_call_handler(tool_call):
                nonlocal skip_files

                log.debug(f'{tool_call=}')

                tool_function_name = tool_call.get('name', None)
                if tool_function_name not in tools:
                    log.warning(f'Tool "{tool_function_name}" not found')
                    return

                tool_function_params = tool_call.get('parameters', {})

                tool = None
                tool_type = ''
                direct_tool = False

                try:
                    tool = tools[tool_function_name]
                    tool_type = tool.get('type', '')
                    direct_tool = tool.get('direct', False)

                    spec = tool.get('spec', {})
                    allowed_params = spec.get('parameters', {}).get('properties', {}).keys()
                    tool_function_params = {k: v for k, v in tool_function_params.items() if k in allowed_params}

                    if tool.get('direct', False):
                        tool_result = await event_caller(
                            {
                                'type': 'execute:tool',
                                'data': {
                                    'id': str(uuid4()),
                                    'name': tool_function_name,
                                    'params': tool_function_params,
                                    'server': tool.get('server', {}),
                                    'session_id': metadata.get('session_id', None),
                                },
                            }
                        )
                    else:
                        tool_function = tool['callable']
                        tool_result = await tool_function(**tool_function_params)

                except Exception as e:
                    tool_result = str(e)

                tool_result, tool_result_files, tool_result_embeds = await process_tool_result(
                    request,
                    tool_function_name,
                    tool_result,
                    tool_type,
                    direct_tool,
                    metadata,
                    user,
                )

                if event_emitter:
                    await terminal_event_handler(
                        tool_function_name,
                        tool_function_params,
                        tool_result,
                        event_emitter,
                    )

                    if tool_result_files:
                        await event_emitter(
                            {
                                'type': 'files',
                                'data': {
                                    'files': tool_result_files,
                                },
                            }
                        )

                    if tool_result_embeds:
                        await event_emitter(
                            {
                                'type': 'embeds',
                                'data': {
                                    'embeds': tool_result_embeds,
                                },
                            }
                        )

                if tool_result:
                    tool = tools[tool_function_name]
                    tool_id = tool.get('tool_id', '')

                    tool_name = f'{tool_id}/{tool_function_name}' if tool_id else f'{tool_function_name}'

                    # Citation is enabled for this tool
                    sources.append(
                        {
                            'source': {
                                'name': (f'{tool_name}'),
                            },
                            'document': [str(tool_result)],
                            'metadata': [
                                {
                                    'source': (f'{tool_name}'),
                                    'parameters': tool_function_params,
                                }
                            ],
                            'tool_result': True,
                        }
                    )

                    if tools[tool_function_name].get('metadata', {}).get('file_handler', False):
                        skip_files = True

            # check if "tool_calls" in result
            if result.get('tool_calls'):
                for tool_call in result.get('tool_calls'):
                    await tool_call_handler(tool_call)
            else:
                await tool_call_handler(result)

        except Exception as e:
            log.debug(f'Error: {e}')
            content = None
    except Exception as e:
        log.debug(f'Error: {e}')
        content = None

    log.debug(f'tool_contexts: {sources}')

    if skip_files and 'files' in body.get('metadata', {}):
        del body['metadata']['files']

    return body, {'sources': sources}


async def chat_web_search_handler(request: Request, form_data: dict, extra_params: dict, user):
    event_emitter = extra_params['__event_emitter__']
    await event_emitter(
        {
            'type': 'status',
            'data': {
                'action': 'web_search',
                'description': 'Searching the web',
                'done': False,
            },
        }
    )

    messages = form_data['messages']
    user_message = get_last_user_message(messages)

    queries = []
    try:
        res = await generate_queries(
            request,
            {
                'model': form_data['model'],
                'messages': messages,
                'prompt': user_message,
                'type': 'web_search',
                'chat_id': extra_params.get('__chat_id__'),
            },
            user,
        )

        # generate_queries returns a JSONResponse on error (e.g. model not
        # found, chat completion failure).  Extract the error detail and
        # re-raise so the outer except block falls back to using the raw
        # user message as the search query.
        if isinstance(res, JSONResponse):
            try:
                error_body = JSONCodec.loads(res.body)
                detail = error_body.get('detail', 'Query generation failed')
            except Exception:
                detail = 'Query generation failed'
            raise Exception(detail)

        response = res['choices'][0]['message']['content']

        try:
            bracket_start = response.rfind('{')
            bracket_end = response.rfind('}') + 1

            if bracket_start == -1 or bracket_end == -1:
                raise Exception('No JSON object found in the response')

            response = response[bracket_start:bracket_end]
            queries = JSONCodec.loads(response)
            queries = queries.get('queries', [])
        except Exception as e:
            queries = [response]

        if ENABLE_QUERIES_CACHE:
            request.state.cached_queries = queries

    except Exception as e:
        log.exception(e)
        queries = [user_message or '']

    # Check if generated queries are empty
    if len(queries) == 1 and queries[0].strip() == '':
        queries = [user_message or '']

    # Check if queries are not found
    if len(queries) == 0:
        await event_emitter(
            {
                'type': 'status',
                'data': {
                    'action': 'web_search',
                    'description': 'No search query generated',
                    'done': True,
                },
            }
        )
        return form_data

    await event_emitter(
        {
            'type': 'status',
            'data': {
                'action': 'web_search_queries_generated',
                'queries': queries,
                'done': False,
            },
        }
    )

    try:
        results = await process_web_search(
            request,
            SearchForm(queries=queries),
            user=user,
        )

        if results:
            files = form_data.get('files', [])

            if results.get('collection_names'):
                for col_idx, collection_name in enumerate(results.get('collection_names')):
                    files.append(
                        {
                            'collection_name': collection_name,
                            'name': ', '.join(queries),
                            'type': 'web_search',
                            'urls': results['filenames'],
                            'queries': queries,
                        }
                    )
            elif results.get('docs'):
                # Invoked when bypass embedding and retrieval is set to True
                docs = results['docs']
                files.append(
                    {
                        'docs': docs,
                        'name': ', '.join(queries),
                        'type': 'web_search',
                        'urls': results['filenames'],
                        'queries': queries,
                    }
                )

            form_data['files'] = files

            await event_emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': 'web_search',
                        'description': 'Searched {{count}} sites',
                        'urls': results['filenames'],
                        'items': results.get('items', []),
                        'done': True,
                    },
                }
            )
        else:
            await event_emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': 'web_search',
                        'description': 'No search results found',
                        'done': True,
                        'error': True,
                    },
                }
            )

    except Exception as e:
        log.exception(e)
        detail = e.detail if isinstance(e, HTTPException) else None
        await event_emitter(
            {
                'type': 'status',
                'data': {
                    'action': 'web_search',
                    'description': (str(detail) if detail else 'An error occurred while searching the web'),
                    'queries': queries,
                    'done': True,
                    'error': True,
                },
            }
        )

    return form_data


def get_images_from_messages(message_list):
    images = []

    for message in reversed(message_list):
        message_images = []
        for file in message.get('files', []):
            if file.get('type') == 'image':
                message_images.append(file.get('url'))
            elif file.get('content_type', '').startswith('image/'):
                message_images.append(file.get('url'))

        if message_images:
            images.append(message_images)

    return images


async def get_image_urls(delta_images, request, metadata, user) -> list[str]:
    if not isinstance(delta_images, list):
        return []

    image_urls = []
    for img in delta_images:
        if not isinstance(img, dict) or img.get('type') != 'image_url':
            continue

        url = img.get('image_url', {}).get('url')
        if not url:
            continue

        if url.startswith('data:image/png;base64'):
            url = await get_image_url_from_base64(request, url, metadata, user)

        image_urls.append(url)

    return image_urls


async def add_file_context(messages: list, chat_id: str, user) -> list:
    """
    Add file URLs to messages for native function calling.
    """
    if not is_saved_chat_id(chat_id):
        return messages

    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        return messages

    history = chat.chat.get('history', {})
    stored_messages = get_message_list(history.get('messages', {}), history.get('currentId'))

    def format_file_tag(file):
        file_id = file.get('id') or file.get('url')
        attrs = f'type="{file.get("type", "file")}"'
        if file_id:
            attrs += f' id="{file_id}"'
        attrs += f' url="{file["url"]}"'
        if file.get('content_type'):
            attrs += f' content_type="{file["content_type"]}"'
        if file.get('name'):
            attrs += f' name="{file["name"]}"'
        return f'<file {attrs}/>'

    # Pair only user-role messages from both lists to avoid misalignment.
    # After process_messages_with_output(), assistant messages with tool calls
    # are expanded into multiple messages (assistant + tool results), making
    # the payload message list longer than the stored message list. A naive
    # positional zip() would pair user messages with wrong stored messages,
    # causing later images to lose their file context (see #21878).
    user_messages = [m for m in messages if m.get('role') == 'user']
    stored_user_messages = [m for m in stored_messages if m.get('role') == 'user']

    for message, stored_message in zip(user_messages, stored_user_messages):
        files_with_urls = [
            file
            for file in stored_message.get('files', [])
            if file.get('url') and not file.get('url').startswith('data:')
        ]
        if not files_with_urls:
            continue

        file_tags = [format_file_tag(file) for file in files_with_urls]
        file_context = '<attached_files>\n' + '\n'.join(file_tags) + '\n</attached_files>\n\n'

        content = message.get('content', '')
        if isinstance(content, list):
            message['content'] = [{'type': 'text', 'text': file_context}] + content
        else:
            message['content'] = file_context + content

    return messages


async def chat_image_generation_handler(request: Request, form_data: dict, extra_params: dict, user):
    metadata = extra_params.get('__metadata__', {})
    chat_id = metadata.get('chat_id', None)
    __event_emitter__ = extra_params.get('__event_emitter__', None)

    if not chat_id or not isinstance(chat_id, str) or not __event_emitter__:
        return form_data

    if not is_saved_chat_id(chat_id):
        message_list = form_data.get('messages', [])
    else:
        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        await __event_emitter__(
            {
                'type': 'status',
                'data': {'description': 'Creating image', 'done': False},
            }
        )

        messages_map = chat.chat.get('history', {}).get('messages', {})
        message_id = chat.chat.get('history', {}).get('currentId')
        message_list = get_message_list(messages_map, message_id)

    user_message = get_last_user_message(message_list)

    prompt = user_message
    message_images = get_images_from_messages(message_list)

    # Limit to first 2 sets of images
    # We may want to change this in the future to allow more images
    input_images = []
    for idx, images in enumerate(message_images):
        if idx >= 2:
            break
        for image in images:
            input_images.append(image)

    system_message_content = ''

    if len(input_images) > 0 and request.app.state.config.ENABLE_IMAGE_EDIT:
        # Edit image(s)
        try:
            images = await image_edits(
                request=request,
                form_data=EditImageForm(**{'prompt': prompt, 'image': input_images}),
                metadata={
                    'chat_id': metadata.get('chat_id', None),
                    'message_id': metadata.get('message_id', None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {'description': 'Image created', 'done': True},
                }
            )

            await __event_emitter__(
                {
                    'type': 'files',
                    'data': {
                        'files': [
                            {
                                'type': 'image',
                                'url': image['url'],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = '<context>The requested image has been edited and created and is now being shown to the user. Let them know that it has been generated.</context>'
        except Exception as e:
            log.debug(e)

            error_message = ''
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get('message', str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {
                        'description': f'An error occurred while generating an image',
                        'done': True,
                    },
                }
            )

            system_message_content = f'<context>Image generation was attempted but failed. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>'

    else:
        # Create image(s)
        if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
            try:
                res = await generate_image_prompt(
                    request,
                    {
                        'model': form_data['model'],
                        'messages': form_data['messages'],
                        'chat_id': metadata.get('chat_id'),
                    },
                    user,
                )

                # Handle JSONResponse from error paths
                if isinstance(res, JSONResponse):
                    try:
                        error_body = JSONCodec.loads(res.body)
                        detail = error_body.get('detail', 'Image prompt generation failed')
                    except Exception:
                        detail = 'Image prompt generation failed'
                    raise Exception(detail)

                response = res['choices'][0]['message']['content']

                try:
                    bracket_start = response.rfind('{')
                    bracket_end = response.rfind('}') + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception('No JSON object found in the response')

                    response = response[bracket_start:bracket_end]
                    response = JSONCodec.loads(response)
                    prompt = response.get('prompt', [])
                except Exception as e:
                    prompt = user_message

            except Exception as e:
                log.exception(e)
                prompt = user_message

        try:
            images = await image_generations(
                request=request,
                form_data=CreateImageForm(**{'prompt': prompt}),
                metadata={
                    'chat_id': metadata.get('chat_id', None),
                    'message_id': metadata.get('message_id', None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {'description': 'Image created', 'done': True},
                }
            )

            await __event_emitter__(
                {
                    'type': 'files',
                    'data': {
                        'files': [
                            {
                                'type': 'image',
                                'url': image['url'],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = '<context>The requested image has been created by the system successfully and is now being shown to the user. Let the user know that the image they requested has been generated and is now shown in the chat.</context>'
        except Exception as e:
            log.debug(e)

            error_message = ''
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get('message', str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {
                        'description': f'An error occurred while generating an image',
                        'done': True,
                    },
                }
            )

            system_message_content = f'<context>Image generation was attempted but failed because of an error. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>'

    if system_message_content:
        form_data['messages'] = add_or_update_system_message(system_message_content, form_data['messages'])

    return form_data


async def chat_completion_files_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    __event_emitter__ = extra_params['__event_emitter__']
    sources = []

    if files := body.get('metadata', {}).get('files', None):
        # Check if all files are in full context mode
        all_full_context = all(item.get('context') == 'full' for item in files)

        queries = []
        if not all_full_context:
            try:
                queries_response = await generate_queries(
                    request,
                    {
                        'model': body['model'],
                        'messages': body['messages'],
                        'type': 'retrieval',
                        'chat_id': body.get('metadata', {}).get('chat_id'),
                    },
                    user,
                )
                queries_response = queries_response['choices'][0]['message']['content']

                try:
                    bracket_start = queries_response.rfind('{')
                    bracket_end = queries_response.rfind('}') + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception('No JSON object found in the response')

                    queries_response = queries_response[bracket_start:bracket_end]
                    queries_response = JSONCodec.loads(queries_response)
                except Exception as e:
                    queries_response = {'queries': [queries_response]}

                queries = queries_response.get('queries', [])
            except Exception:
                pass

            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {
                        'action': 'queries_generated',
                        'queries': queries,
                        'done': False,
                    },
                }
            )

        if len(queries) == 0:
            queries = [get_last_user_message(body['messages']) or '']

        try:
            # Directly await async get_sources_from_items (no thread needed - fully async now)
            sources = await get_sources_from_items(
                request=request,
                items=files,
                queries=queries,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=request.app.state.config.RAG_TOP_K,
                reranking_function=(
                    (lambda query, documents: request.app.state.RERANKING_FUNCTION(query, documents, user=user))
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=request.app.state.config.RAG_TOP_K_RERANKER,
                r=request.app.state.config.RAG_RELEVANCE_THRESHOLD,
                hybrid_bm25_weight=request.app.state.config.RAG_HYBRID_BM25_WEIGHT,
                hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                full_context=all_full_context or request.app.state.config.RAG_FULL_CONTEXT,
                user=user,
            )
        except Exception as e:
            log.exception(e)

        log.debug(f'rag_contexts:sources: {sources}')

        unique_ids = set()
        for source in sources or []:
            if not source or len(source.keys()) == 0:
                continue

            documents = source.get('document') or []
            metadatas = source.get('metadata') or []
            src_info = source.get('source') or {}

            for index, _ in enumerate(documents):
                metadata = metadatas[index] if index < len(metadatas) else None
                _id = (metadata or {}).get('source') or (src_info or {}).get('id') or 'N/A'
                unique_ids.add(_id)

        sources_count = len(unique_ids)
        await __event_emitter__(
            {
                'type': 'status',
                'data': {
                    'action': 'sources_retrieved',
                    'count': sources_count,
                    'done': True,
                },
            }
        )

    return body, {'sources': sources}


def apply_params_to_form_data(form_data, model):
    params = form_data.pop('params', {})
    custom_params = params.pop('custom_params', {})

    open_webui_params = {
        'stream_response': bool,
        'stream_delta_chunk_size': int,
        'function_calling': str,
        'reasoning_tags': list,
        'compact_token_threshold': int,
        'system': str,
        'note_id': str,
    }

    for key in list(params.keys()):
        if key in open_webui_params:
            del params[key]

    if custom_params:
        # Attempt to parse custom_params if they are strings
        for key, value in custom_params.items():
            if isinstance(value, str):
                try:
                    # Attempt to parse the string as JSON
                    custom_params[key] = JSONCodec.loads(value)
                except json.JSONDecodeError:
                    # If it fails, keep the original string
                    pass

        # If custom_params are provided, merge them into params
        params = deep_update(params, custom_params)

    if model.get('owned_by') == 'ollama':
        # Ollama specific parameters
        form_data['options'] = params
    else:
        if isinstance(params, dict):
            for key, value in params.items():
                if value is not None:
                    form_data[key] = value

        if 'logit_bias' in params and params['logit_bias'] is not None:
            try:
                logit_bias = convert_logit_bias_input_to_json(params['logit_bias'])

                if logit_bias:
                    form_data['logit_bias'] = JSONCodec.loads(logit_bias)
            except Exception as e:
                log.exception(f'Error parsing logit_bias: {e}')

    return form_data


async def convert_url_images_to_base64(form_data, user=None):
    messages = form_data.get('messages', [])

    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            continue

        new_content = []

        for item in content:
            if not isinstance(item, dict) or item.get('type') != 'image_url':
                new_content.append(item)
                continue

            image_url = item.get('image_url', {}).get('url', '')
            if image_url.startswith('data:image/'):
                new_content.append(item)
                continue

            try:
                base64_data = await get_image_base64_from_url(image_url, user=user)
                if base64_data:
                    new_content.append(
                        {
                            'type': 'image_url',
                            'image_url': {'url': base64_data},
                        }
                    )
                else:
                    new_content.append(item)
            except Exception as e:
                log.debug(f'Error converting image URL to base64: {e}')
                new_content.append(item)

        message['content'] = new_content

    return form_data


async def load_messages_from_db(chat_id: str, message_id: str) -> Optional[list[dict]]:
    """
    Load the message chain from DB up to message_id,
    keeping only LLM-relevant fields (role, content, output).
    """
    messages_map = await Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    return [
        {k: v for k, v in msg.items() if k in ('id', 'role', 'content', 'output', 'files', 'contextSummary', 'usage')}
        for msg in db_messages
    ]


def get_reasoning_format(model: dict) -> str | None:
    """
    Determine how reasoning should be included in reconstructed messages.

    Returns:
        'think_tags': Ollama expects <think> tags in content.
        'reasoning_content': llama.cpp supports reasoning_content as a top-level field.
        None: skip reasoning (safe default for strict providers).
    """
    provider = model.get('provider', '')
    if provider == 'ollama':
        return 'think_tags'
    if provider == 'llama.cpp':
        return 'reasoning_content'
    return None


def process_messages_with_output(
    messages: list[dict],
    reasoning_format: str | None = None,
) -> list[dict]:
    """
    Process messages with OR-aligned output items for LLM consumption.

    For assistant messages with 'output' field, produces properly formatted
    OpenAI-style messages (tool_calls + tool results). Strips 'output' before LLM.
    """
    processed = []

    for message in messages:
        if message.get('role') == 'assistant' and message.get('output'):
            # Use output items for clean OpenAI-format messages
            output_messages = convert_output_to_messages(
                message['output'],
                raw=True,
                reasoning_format=reasoning_format,
                flatten_tool_images=True,
            )
            if output_messages:
                processed.extend(output_messages)
                continue

        # Strip 'output' field before adding (LLM shouldn't see it)
        clean_message = {k: v for k, v in message.items() if k != 'output'}
        processed.append(clean_message)

    return processed


def strip_compaction_fields(messages: list[dict]) -> list[dict]:
    stripped = []
    for message in messages:
        clean = dict(message)
        clean.pop('contextSummary', None)
        clean.pop('context_summary', None)
        clean.pop('usage', None)
        clean.pop('id', None)
        stripped.append(clean)
    return stripped


def sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    tool_result_ids = {
        message.get('tool_call_id')
        for message in messages
        if message.get('role') == 'tool' and message.get('tool_call_id')
    }

    tool_call_ids = {
        tool_call.get('id')
        for message in messages
        for tool_call in (message.get('tool_calls') or [])
        if message.get('role') == 'assistant' and tool_call.get('id')
    }

    sanitized = []
    for message in messages:
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            kept = [
                tool_call for tool_call in message.get('tool_calls') or [] if tool_call.get('id') in tool_result_ids
            ]
            if kept:
                sanitized.append({**message, 'tool_calls': kept})
            else:
                clean = dict(message)
                clean.pop('tool_calls', None)
                clean.pop('reasoning_items', None)
                if clean.get('content'):
                    sanitized.append(clean)
        elif message.get('role') != 'tool' or message.get('tool_call_id') in tool_call_ids:
            sanitized.append(message)

    return sanitized


SKILL_MENTION_RE = re.compile(r'<(?:\$([^|>]+)(?:\|[^>]*)?|/([^|>]+)\|[^>]*)>')


def _get_text_parts(message: dict) -> list[str]:
    """Return all text segments from a message's content."""
    content = message.get('content')
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
    return []


def extract_skill_ids_from_messages(messages: list[dict]) -> set[str]:
    """Extract skill IDs from <$skillId|label> and </skillId|label> mention tags."""
    ids: set[str] = set()
    for message in messages:
        for text in _get_text_parts(message):
            ids.update(m.group(1) or m.group(2) for m in SKILL_MENTION_RE.finditer(text))
    return ids


SKILL_MENTION_STRIP_RE = re.compile(r'<(?:\$[^|>]+(?:\|([^>]*))?|/[^|>]+\|([^>]*))>')


def strip_skill_mentions(messages: list[dict]) -> None:
    """Replace <$skillId|label> and </skillId|label> mention tags with the label in-place."""

    def label(match):
        return match.group(1) or match.group(2) or ''

    for message in messages:
        content = message.get('content')
        if isinstance(content, str) and SKILL_MENTION_STRIP_RE.search(content):
            message['content'] = SKILL_MENTION_STRIP_RE.sub(label, content).strip()
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'text':
                    text = part.get('text', '')
                    if SKILL_MENTION_STRIP_RE.search(text):
                        part['text'] = SKILL_MENTION_STRIP_RE.sub(label, text).strip()


async def connect_mcp_server(
    request,
    server_id: str,
    user,
    metadata: dict,
    extra_params: dict,
) -> tuple[MCPClient, list[dict]] | None:
    """Resolve an MCP server connection, authenticate, and return (client, tool_specs).

    Returns None if the server is not found or access is denied.
    """
    mcp_server_connection = None
    for server_connection in request.app.state.config.TOOL_SERVER_CONNECTIONS or []:
        if server_connection.get('type', '') == 'mcp' and server_connection.get('info', {}).get('id') == server_id:
            mcp_server_connection = server_connection
            break

    if not mcp_server_connection:
        log.error(f'MCP server with id {server_id} not found')
        return None

    if not await has_connection_access(user, mcp_server_connection):
        log.warning(f'Access denied to MCP server {server_id} for user {user.id}')
        return None

    headers, _ = await build_tool_server_headers(
        mcp_server_connection,
        request,
        user,
        server_id=server_id,
        metadata=metadata,
        extra_params=extra_params,
    )

    client = MCPClient()
    await client.connect(
        url=mcp_server_connection.get('url', ''),
        headers=headers if headers else None,
    )

    function_name_filter_list = mcp_server_connection.get('config', {}).get('function_name_filter_list', '')
    if isinstance(function_name_filter_list, str):
        function_name_filter_list = function_name_filter_list.split(',')

    tool_specs = await client.list_tool_specs()
    if function_name_filter_list:
        tool_specs = [spec for spec in tool_specs if is_string_allowed(spec['name'], function_name_filter_list)]

    return client, tool_specs


async def process_chat_payload(request, form_data, user, metadata, model):
    # Ensure chat_id is always a string — external API clients may omit it.
    if not isinstance(metadata.get('chat_id'), str):
        metadata['chat_id'] = ''

    # Pipeline Inlet -> Filter Inlet -> Chat Memory -> Chat Web Search -> Chat Image Generation
    # -> Chat Code Interpreter (Form Data Update) -> (Default) Chat Tools Function Calling
    # -> Chat Files

    # Arena model resolution — pick the sub-model now so all downstream
    # processing (knowledge, capabilities, tools, params) uses its settings
    # instead of the empty arena wrapper.
    if model.get('owned_by') == 'arena':
        arena_model_ids = model.get('info', {}).get('meta', {}).get('model_ids')
        arena_filter_mode = model.get('info', {}).get('meta', {}).get('filter_mode')
        if arena_model_ids and arena_filter_mode == 'exclude':
            arena_model_ids = [
                available_model['id']
                for available_model in request.app.state.MODELS.values()
                if available_model.get('owned_by') != 'arena' and available_model['id'] not in arena_model_ids
            ]

        if isinstance(arena_model_ids, list) and arena_model_ids:
            selected_model_id = random.choice(arena_model_ids)
        else:
            arena_model_ids = [
                available_model['id']
                for available_model in request.app.state.MODELS.values()
                if available_model.get('owned_by') != 'arena'
            ]
            selected_model_id = random.choice(arena_model_ids)

        selected_model = request.app.state.MODELS.get(selected_model_id)
        if selected_model:
            model = selected_model
            form_data['model'] = selected_model_id
            metadata['selected_model_id'] = selected_model_id

    # Captured before apply_params_to_form_data pops 'params'; feeds metadata['system_prompt'] below
    model_system_prompt = (form_data.get('params') or {}).get('system')

    form_data = apply_params_to_form_data(form_data, model)
    log.debug(f'form_data: {form_data}')

    # Guided regeneration: extract before it reaches the LLM provider
    regeneration_prompt = form_data.pop('regeneration_prompt', None)

    # Load messages from DB when available — DB preserves structured 'output' items
    # which the frontend strips, causing tool calls to be merged into content.
    chat_id = metadata.get('chat_id')
    user_message_id = metadata.get('user_message_id')

    if is_saved_chat_id(chat_id) and user_message_id:
        db_messages = await load_messages_from_db(chat_id, user_message_id)
        if db_messages:
            # Continue: frontend sends assistant_message_id when continuing
            # an existing response. Load its content so the LLM sees prior output.
            assistant_message_id = metadata.get('assistant_message_id')
            if assistant_message_id:
                assistant_message = await Chats.get_message_by_id_and_message_id(chat_id, assistant_message_id)
                if assistant_message and (assistant_message.get('content') or assistant_message.get('output')):
                    db_messages.append(
                        {
                            k: v
                            for k, v in assistant_message.items()
                            if k in ('id', 'role', 'content', 'output', 'files', 'contextSummary', 'usage')
                        }
                    )

            system_message = get_system_message(form_data.get('messages', []))
            form_data['messages'] = [system_message, *db_messages] if system_message else db_messages

            # Inject image files into content as image_url parts (mirrors frontend logic)
            for message in form_data['messages']:
                image_files = [
                    f
                    for f in message.get('files', [])
                    if f.get('type') == 'image' or (f.get('content_type') or '').startswith('image/')
                ]
                if message.get('role') == 'user' and image_files:
                    text_content = message.get('content', '')
                    if isinstance(text_content, str):
                        message['content'] = [
                            {'type': 'text', 'text': text_content},
                            *[
                                {
                                    'type': 'image_url',
                                    'image_url': {'url': f['url']},
                                }
                                for f in image_files
                                if f.get('url')
                            ],
                        ]
                # Strip files field — it's been incorporated into content
                message.pop('files', None)

    if regeneration_prompt:
        form_data['messages'].append({'role': 'user', 'content': regeneration_prompt})

    if is_saved_chat_id(chat_id) and user_message_id:
        if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
            compaction_models = {
                **request.app.state.MODELS,
                request.state.model['id']: request.state.model,
            }
        else:
            compaction_models = request.app.state.MODELS

        system_message = get_system_message(form_data.get('messages', []))
        system_prompt = get_content_from_message(system_message) if system_message else ''

        try:
            form_data['messages'], context_summary, _ = await compact_messages_for_request(
                request,
                user,
                form_data.get('messages', []),
                metadata,
                form_data.get('model'),
                compaction_models,
                system_prompt,
            )
            if context_summary:
                form_data['messages'] = add_or_update_system_message(
                    f'[CONVERSATION SUMMARY]\n{context_summary}',
                    form_data['messages'],
                    append=True,
                )
        except Exception:
            log.exception('Context compaction failed; continuing with full chat history')

    form_data['messages'] = strip_compaction_fields(form_data.get('messages', []))

    # Process messages with OR-aligned output items for clean LLM messages
    form_data['messages'] = process_messages_with_output(
        form_data.get('messages', []),
        reasoning_format=get_reasoning_format(model),
    )
    form_data['messages'] = sanitize_tool_pairs(form_data['messages'])

    system_message = get_system_message(form_data.get('messages', []))
    if system_message:  # Chat Controls/User Settings
        try:
            form_data = await apply_system_prompt_to_body(
                system_message.get('content'), form_data, metadata, user, replace=True
            )  # Required to handle system prompt variables
        except Exception:
            pass

    form_data = await convert_url_images_to_base64(form_data, user=user)

    event_emitter = await get_event_emitter(metadata)
    event_caller = await get_event_call(metadata)

    extra_params = {
        '__event_emitter__': event_emitter,
        '__event_call__': event_caller,
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__oauth_token__': await get_system_oauth_token(request, user),
        '__request__': request,
        '__model__': model,
        '__chat_id__': metadata.get('chat_id'),
        '__message_id__': metadata.get('message_id'),
    }
    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    task_model_id = get_task_model_id(
        form_data['model'],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    events = []
    sources = []

    # Folder "Project" handling
    # Check if the request has chat_id and is inside of a folder
    # Uses lightweight column query — only fetches folder_id, not the full chat JSON blob
    chat_id = metadata.get('chat_id', None)
    folder_id = None
    if user and is_saved_chat_id(chat_id):
        folder_id = await Chats.get_chat_folder_id(chat_id, user.id)

    # Fallback: use folder_id from metadata (temporary chats have no DB record)
    if not folder_id:
        folder_id = metadata.get('folder_id', None)

    if folder_id and user:
        folder = await Folders.get_folder_by_id(folder_id)
        if folder and user.role != 'admin' and not await has_folder_access(user.id, folder, 'read', db=None):
            folder = None

        if folder and folder.data:
            if 'system_prompt' in folder.data:
                form_data = await apply_system_prompt_to_body(folder.data['system_prompt'], form_data, metadata, user)
            if 'files' in folder.data:
                if metadata.get('params', {}).get('function_calling') == 'legacy':
                    form_data['files'] = [
                        {'type': 'folder', 'id': folder.id},
                        *form_data.get('files', []),
                    ]
                else:
                    # Native FC: skip RAG injection, builtin tools
                    # will read folder knowledge from metadata.
                    metadata['folder_knowledge'] = await get_owner_accessible_folder_files(folder)

    # Model "Knowledge" handling
    user_message = get_last_user_message(form_data['messages'])
    model_knowledge = model.get('info', {}).get('meta', {}).get('knowledge', False)

    if model_knowledge and metadata.get('params', {}).get('function_calling') == 'legacy':
        await event_emitter(
            {
                'type': 'status',
                'data': {
                    'action': 'knowledge_search',
                    'query': user_message,
                    'done': False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get('collection_name'):
                knowledge_files.append(
                    {
                        'id': item.get('collection_name'),
                        'name': item.get('name'),
                        'legacy': True,
                    }
                )
            elif item.get('collection_names'):
                knowledge_files.append(
                    {
                        'name': item.get('name'),
                        'type': 'collection',
                        'collection_names': item.get('collection_names'),
                        'legacy': True,
                    }
                )
            else:
                knowledge_files.append(item)

        files = form_data.get('files', [])
        files.extend(knowledge_files)
        form_data['files'] = files

    variables = form_data.pop('variables', None)
    payload_tools = form_data.get('tools', None)  # snapshot before filters

    # Process the form_data through the pipeline. The fail-closed PII guard
    # (refuse when masking is requested but no PII filter can be applied) lives
    # INSIDE process_pipeline_inlet_filter so it covers every inlet caller —
    # this main-chat path AND all task generators — from a single chokepoint.
    try:
        form_data = await process_pipeline_inlet_filter(request, form_data, user, models)
    except Exception as e:
        raise e

    if ENABLE_PLUGINS:
        try:
            filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))

            form_data, flags = await process_filter_functions(
                request=request,
                filter_context=None,
                filter_functions=filter_functions,
                filter_type='inlet',
                form_data=form_data,
                extra_params=extra_params,
            )
        except Exception as e:
            raise Exception(f'{e}')

    features = form_data.pop('features', None) or {}
    extra_params['__features__'] = features
    if features:
        if 'voice' in features and features['voice']:
            if getattr(request.app.state.config, 'ENABLE_VOICE_MODE_PROMPT', True):
                if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE:
                    template = request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE
                else:
                    template = DEFAULT_VOICE_MODE_PROMPT_TEMPLATE

                form_data['messages'] = add_or_update_system_message(
                    template,
                    form_data['messages'],
                )

        if 'memory' in features and features['memory'] and request.app.state.config.ENABLE_MEMORY_SYSTEM_CONTEXT:
            form_data = await add_memory_context(request, form_data, user, model)

        if 'web_search' in features and features['web_search']:
            # features is client-supplied; re-check the permission the native FC path enforces.
            if getattr(user, 'role', None) == 'admin' or await has_permission(
                getattr(user, 'id', ''),
                'features.web_search',
                request.app.state.config.USER_PERMISSIONS,
            ):
                # Skip forced RAG web search when native FC is enabled - model can use web_search tool
                if metadata.get('params', {}).get('function_calling') == 'legacy':
                    form_data = await chat_web_search_handler(request, form_data, extra_params, user)

        if 'image_generation' in features and features['image_generation']:
            # features is client-supplied; re-check the permission the direct /images routes enforce.
            if getattr(user, 'role', None) == 'admin' or await has_permission(
                getattr(user, 'id', ''),
                'features.image_generation',
                request.app.state.config.USER_PERMISSIONS,
            ):
                # Skip forced image generation when native FC is enabled - model can use generate_image tool
                if metadata.get('params', {}).get('function_calling') == 'legacy':
                    form_data = await chat_image_generation_handler(request, form_data, extra_params, user)

        if 'code_interpreter' in features and features['code_interpreter']:
            engine = request.app.state.config.CODE_INTERPRETER_ENGINE

            # Skip XML-tag prompt injection when native FC is enabled —
            # execute_code will be injected as a builtin tool instead
            if metadata.get('params', {}).get('function_calling') == 'legacy':
                ci_prompt_template = request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                prompt = ci_prompt_template if ci_prompt_template != '' else DEFAULT_CODE_INTERPRETER_PROMPT

                # Append filesystem awareness only for pyodide engine
                if engine != 'jupyter':
                    prompt += CODE_INTERPRETER_PYODIDE_PROMPT

                form_data['messages'] = add_or_update_user_message(
                    prompt,
                    form_data['messages'],
                )
            else:
                # Native FC: tool docstring can't be dynamic, so inject
                # filesystem context into the system message for pyodide
                # engine.  Appending to the system prompt (instead of the
                # user message) keeps it in the stable cached prefix so
                # providers with prefix caching don't re-bill the full
                # conversation on every turn.
                if engine != 'jupyter':
                    form_data['messages'] = add_or_update_system_message(
                        CODE_INTERPRETER_PYODIDE_PROMPT,
                        form_data['messages'],
                        append=True,
                    )

    tool_ids = form_data.pop('tool_ids', None)
    terminal_id = form_data.pop('terminal_id', None)
    files = form_data.pop('files', None)
    form_data.pop('folder_id', None)

    # If the original caller provided tools, use them as-is (skip resolution).
    # Otherwise, save any tools that filter inlets added for merging later.
    inlet_filter_tools = None if payload_tools is not None else form_data.get('tools', None)

    # Mentioned skills get full content; selected/default skills can be loaded through view_skill.
    mentioned_skill_ids = extract_skill_ids_from_messages(form_data.get('messages', []))
    skill_ids = sorted(
        set(form_data.pop('skill_ids', None) or [])
        | set(model.get('info', {}).get('meta', {}).get('skillIds', []))
        | mentioned_skill_ids
    )
    available_skills = []
    view_skill_ids = []
    chat = None
    if is_saved_chat_id(metadata.get('chat_id')):
        chat = await Chats.get_chat_by_id(metadata['chat_id'])

    if chat and (chat.meta or {}).get('internal') is True and (chat.meta or {}).get('type') == 'note':
        note_id = (chat.meta or {}).get('note_id')
        note = await Notes.get_note_by_id(note_id) if note_id else None
        if note and (
            user.role == 'admin'
            or note.user_id == user.id
            or await AccessGrants.has_access(
                user_id=user.id,
                resource_type='note',
                resource_id=note.id,
                permission='read',
            )
        ):
            note_files = [
                file
                for file in ((note.data or {}).get('files') or [])
                if isinstance(file, dict)
                and file.get('type') != 'image'
                and not (file.get('content_type') or '').startswith('image/')
            ]
            if note_files:
                files = [*(files or []), *note_files]

    use_builtin_tools = (
        chat and (chat.meta or {}).get('internal') is True and (chat.meta or {}).get('type') == 'note'
    ) or (
        bool(metadata.get('session_id'))
        and metadata.get('params', {}).get('function_calling') != 'legacy'
        and (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('builtin_tools', True)
    )

    if skill_ids:
        from open_webui.models.skills import Skills as SkillsModel

        # Reuse the rows from the access query instead of re-fetching each
        # skill by id.
        accessible_skills = {s.id: s for s in await SkillsModel.get_skills_by_user_id(user.id, 'read')}
        for sid in skill_ids:
            s = accessible_skills.get(sid)
            if s and s.is_active:
                available_skills.append(s)

        skill_manifest = ''
        for skill in available_skills:
            if skill.id in mentioned_skill_ids or not use_builtin_tools:
                form_data['messages'] = add_or_update_system_message(
                    f'<skill name="{skill.name}">\n{skill.content}\n</skill>',
                    form_data['messages'],
                    append=True,
                )
            else:
                view_skill_ids.append(skill.id)
                skill_manifest += (
                    f'<skill>\n<id>{skill.id}</id>\n<name>{skill.name}</name>\n'
                    f'<description>{skill.description or ""}</description>\n</skill>\n'
                )

        if skill_manifest:
            form_data['messages'] = add_or_update_system_message(
                f'<available_skills>\n{skill_manifest}</available_skills>',
                form_data['messages'],
                append=True,
            )

    # Strip <$skillId|label> mention tags so the model doesn't see raw markup.
    strip_skill_mentions(form_data.get('messages', []))

    prompt = get_last_user_message(form_data['messages'])

    # Guard against empty user message after skill mention stripping.
    # When a user selects a skill ($skill-name) without typing additional text,
    # the stripped result is an empty string which causes 400 errors on providers
    # that reject empty content blocks (e.g. AWS Bedrock ConverseStream).
    if not prompt or not prompt.strip():
        fallback = ', '.join(s.name for s in available_skills)
        if fallback:
            set_last_user_message_content(fallback, form_data['messages'])
            prompt = fallback
    # TODO: re-enable URL extraction from prompt
    # urls = []
    # if prompt and len(prompt or "") < 500 and (not files or len(files) == 0):
    #     urls = extract_urls(prompt)

    if files:
        for file_item in files:
            if file_item.get('type', 'file') == 'folder':
                # Get folder files
                folder_id = file_item.get('id', None)
                if folder_id:
                    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user.id)
                    if folder and folder.data and 'files' in folder.data:
                        files = [f for f in files if f.get('id', None) != folder_id]
                        files = [*files, *await get_accessible_folder_files(folder.data['files'], user)]

        # files = [*files, *[{"type": "url", "url": url, "name": url} for url in urls]]
        # Remove duplicate files based on their content
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    # Keeper PII card: capture the pipeline-provided detection summary BEFORE
    # OWUI rebuilds `form_data["metadata"]` (which would otherwise drop it).
    # Only the slim, non-PII `pii_detections_public` ([{type,start,end}]) is
    # carried forward — never `pii_detections`/`pii_reverse_map`/
    # `pii_placeholder_map`, which contain plaintext originals + placeholders.
    pipeline_md = form_data.get("metadata") or {}
    # Defense-in-depth: re-whitelist to exactly {type:str, start:int, end:int}
    # right at the trust boundary (data entering OWUI from the separately
    # deployed pipeline). Even if the pipeline ever emits extra keys (e.g. a
    # plaintext `original`) or malformed offsets, nothing beyond {type,start,end}
    # can reach the socket event or the persisted chat record.
    pii_public = [
        {"type": d["type"], "start": d["start"], "end": d["end"]}
        for d in (pipeline_md.get("pii_detections_public") or [])
        if isinstance(d, dict)
        and isinstance(d.get("type"), str)
        and isinstance(d.get("start"), int)
        and not isinstance(d.get("start"), bool)
        and isinstance(d.get("end"), int)
        and not isinstance(d.get("end"), bool)
    ]

    metadata.update(
        {
            'model_id': form_data.get('model'),
            'tool_ids': tool_ids,
            'skill_ids': skill_ids,
            'terminal_id': terminal_id,
            'files': files,
            'features': features,
        }
    )
    if pii_public:
        metadata["pii_detections_public"] = pii_public
        log.info(
            "pii_card bridge: captured %d detection(s) from pipeline metadata",
            len(pii_public),
        )
    form_data['metadata'] = metadata

    # When the caller provides an explicit `tools` key in the request body,
    # skip all server-side tool resolution and pass the caller's tools through
    # unchanged.  Sending `tools: []` explicitly opts out of builtin injection.
    if payload_tools is None:
        # Server side tools
        tool_ids = metadata.get('tool_ids', None)
        # Client side tools
        direct_tool_servers = metadata.get('tool_servers', None)

        log.debug(f'{tool_ids=}')
        log.debug(f'{direct_tool_servers=}')

        tools_dict = {}

        mcp_clients = {}
        mcp_tools_dict = {}

        if tool_ids:
            db_tool_ids = []
            for tool_id in tool_ids:
                if tool_id.startswith('server:mcp:'):
                    try:
                        server_id = tool_id[len('server:mcp:') :]

                        result = await connect_mcp_server(
                            request,
                            server_id,
                            user,
                            metadata,
                            extra_params,
                        )
                        if result is None:
                            continue

                        client, tool_specs = result
                        mcp_clients[server_id] = client

                        for tool_spec in tool_specs:

                            async def make_tool_function(client, function_name):
                                async def tool_function(**kwargs):
                                    return await client.call_tool(
                                        function_name,
                                        function_args=kwargs,
                                    )

                                return tool_function

                            tool_function = await make_tool_function(client, tool_spec['name'])

                            mcp_tools_dict[f'{server_id}_{tool_spec["name"]}'] = {
                                'spec': {
                                    **tool_spec,
                                    'name': f'{server_id}_{tool_spec["name"]}',
                                },
                                'callable': tool_function,
                                'type': 'mcp',
                                'client': client,
                                'direct': False,
                            }
                    except Exception as e:
                        log.debug(e)
                        if event_emitter:
                            await event_emitter(
                                {
                                    'type': 'chat:message:error',
                                    'data': {'error': {'content': f"Failed to connect to MCP server '{server_id}'"}},
                                }
                            )
                        continue
                elif ENABLE_PLUGINS:
                    db_tool_ids.append(tool_id)

            if db_tool_ids:
                tools_dict = await get_tools(
                    request,
                    db_tool_ids,
                    user,
                    {
                        **extra_params,
                        '__model__': models[task_model_id],
                        '__messages__': form_data['messages'],
                        '__files__': metadata.get('files', []),
                    },
                )

            if mcp_tools_dict:
                tools_dict = {**tools_dict, **mcp_tools_dict}

        # Resolve terminal tools if terminal_id is set (outside tool_ids check
        # so system terminals work even when no other tools are selected)
        terminal_capability = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('terminal', True)
        if terminal_id and terminal_capability:
            try:
                terminal_result = await get_terminal_tools(
                    request,
                    terminal_id,
                    user,
                    extra_params,
                )
                if isinstance(terminal_result, tuple):
                    terminal_tools, system_prompt = terminal_result
                else:
                    terminal_tools = terminal_result
                    system_prompt = None
                if terminal_tools:
                    tools_dict = {**tools_dict, **terminal_tools}
                if system_prompt:
                    form_data['messages'] = add_or_update_system_message(
                        system_prompt,
                        form_data['messages'],
                        append=True,
                    )
            except Exception as e:
                log.exception(e)
                raise HTTPException(status_code=503, detail=f'Terminal unavailable: {e}') from e

        if direct_tool_servers:
            for tool_server in direct_tool_servers:
                system_prompt = tool_server.pop('system_prompt', None)
                if system_prompt:
                    form_data['messages'] = add_or_update_system_message(
                        system_prompt,
                        form_data['messages'],
                        append=True,
                    )

                tool_specs = tool_server.pop('specs', [])

                for tool in tool_specs:
                    tools_dict[tool['name']] = {
                        'spec': tool,
                        'direct': True,
                        'server': tool_server,
                    }

        if mcp_clients:
            metadata['mcp_clients'] = mcp_clients

        # Inject builtin tools for native function calling based on enabled features and model capability.
        # Only inject when the request originates from the UI (identified by session_id).
        # API callers don't expect hidden tools; they can explicitly request tools via tool_ids.
        if use_builtin_tools:
            # Add file context to user messages
            chat_id = metadata.get('chat_id')
            form_data['messages'] = await add_file_context(form_data.get('messages', []), chat_id, user)

            if (model.get('info', {}).get('meta', {}).get('builtinTools') or {}).get('knowledge', True):
                from html import escape

                knowledge_tags = []
                for item in get_attached_knowledge(model, metadata):
                    if not item.get('id') or not item.get('type'):
                        continue
                    attrs = f'type="{escape(str(item["type"]), quote=True)}" id="{escape(str(item["id"]), quote=True)}"'
                    if item.get('name'):
                        attrs += f' name="{escape(str(item["name"]), quote=True)}"'
                    if item.get('source'):
                        attrs += f' source="{escape(str(item["source"]), quote=True)}"'
                    knowledge_tags.append(f'<knowledge {attrs}/>')

                if knowledge_tags:
                    form_data['messages'] = add_or_update_system_message(
                        '<attached_knowledge>\n' + '\n'.join(knowledge_tags) + '\n</attached_knowledge>',
                        form_data['messages'],
                        append=True,
                    )

            builtin_tools = await get_builtin_tools(
                request,
                {
                    **extra_params,
                    '__event_emitter__': event_emitter,
                    '__skill_ids__': view_skill_ids,
                },
                features,
                model,
            )
            for name, tool_dict in builtin_tools.items():
                if name not in tools_dict:
                    tools_dict[name] = tool_dict

        if tools_dict:
            # Always store resolved tools in metadata so downstream consumers
            # (e.g. pipe functions) can access all tools including MCP and builtins.
            metadata['tools'] = tools_dict

            if metadata.get('params', {}).get('function_calling') != 'legacy':
                # If the function calling is native, then call the tools function calling handler
                form_data['tools'] = [
                    {'type': 'function', 'function': tool.get('spec', {})} for tool in tools_dict.values()
                ]
                if inlet_filter_tools:
                    form_data['tools'].extend(inlet_filter_tools)
            else:
                # If the function calling is not native, then call the tools function calling handler
                try:
                    form_data, flags = await chat_completion_tools_handler(
                        request, form_data, extra_params, user, models, tools_dict
                    )
                    sources.extend(flags.get('sources', []))
                except Exception as e:
                    log.exception(e)

    # Check if file context extraction is enabled for this model (default True)
    file_context_enabled = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('file_context', True)

    if file_context_enabled:
        try:
            form_data, flags = await chat_completion_files_handler(request, form_data, extra_params, user)
            sources.extend(flags.get('sources', []))
        except Exception as e:
            log.exception(e)

    # Save the pre-RAG message state so the native tool call loop can
    # restore to the true original (before file-source injection) rather
    # than a snapshot that already has the RAG template baked in.
    system_message = get_system_message(form_data['messages'])
    system_content = get_content_from_message(system_message) if system_message else ''
    resolved_model_system_prompt = await resolve_system_prompt(
        model_system_prompt,
        metadata,
        user,
    )
    if resolved_model_system_prompt:
        system_content = (
            f'{resolved_model_system_prompt}\n{system_content}' if system_content else resolved_model_system_prompt
        )
    metadata['system_prompt'] = system_content or None
    metadata['user_prompt'] = get_last_user_message(form_data['messages'])
    metadata['sources'] = sources[:] if sources else []
    # PII-masked mirror of metadata['sources'], filled in below once masking has
    # run. Anything that re-renders file sources INTO the prompt must read this
    # key, never metadata['sources'] (which stays original for the citations the
    # frontend shows). Empty until masking succeeds → fail-closed by default.
    metadata['masked_sources'] = []

    # If context is not empty, insert it into the messages
    if sources and prompt:
        form_data['messages'], file_pii, masked_sources = await apply_source_context_to_messages(
            request,
            form_data['messages'],
            sources,
            prompt,
            chat_id=chat_id,
            user=user,
            model_id=form_data['model'],
            models=models,
            features=features,
            # Same signal, same renderer as a large paste: masking a document
            # costs the same minutes and used to show nothing at all.
            on_progress=(_pii_progress_emitter(event_emitter) if event_emitter else None),
        )
        # The native tool-call loop re-renders metadata['sources'] into the RAG
        # template. Hand it the PII-masked copy so that re-render cannot put
        # unmasked file text in front of the LLM. metadata['sources'] itself
        # stays original — it feeds the frontend citations that B2 uses to
        # reconstruct masked values client-side.
        metadata['masked_sources'] = masked_sources
        # B2: merge file-sourced detections into the same slim channel as
        # message-PII. Each carries {type,start,end,fileId,fileName,docIdx} —
        # no value (boundary); the frontend reconstructs it from the citation.
        if file_pii:
            metadata['pii_detections_public'] = (metadata.get('pii_detections_public') or []) + file_pii

    # If there are citations, add them to the data_items
    sources = [
        source
        for source in sources
        if source.get('source', {}).get('name', '') or source.get('source', {}).get('id', '')
    ]

    if len(sources) > 0:
        events.append({'sources': sources})

    if model_knowledge:
        await event_emitter(
            {
                'type': 'status',
                'data': {
                    'action': 'knowledge_search',
                    'query': user_message,
                    'done': True,
                    'hidden': True,
                },
            }
        )

    # Strip empty text content blocks from multimodal messages
    # to prevent errors from providers like Gemini and Claude
    form_data['messages'] = strip_empty_content_blocks(form_data.get('messages', []))

    # Merge any duplicate system messages into a single message at position 0
    # to prevent template parsing errors with strict chat templates (e.g. Qwen)
    form_data['messages'] = merge_system_messages(form_data.get('messages', []))

    return form_data, metadata, events


async def get_event_emitter_and_caller(metadata):
    event_emitter = None
    event_caller = None

    # event_emitter only needs user_id + chat_id + message_id.
    # It broadcasts to user:{user_id} room AND persists to DB,
    # so it works for backend-initiated calls (automations, API).
    if metadata.get('chat_id') and metadata.get('message_id'):
        event_emitter = await get_event_emitter(metadata)

    # event_caller needs session_id — it calls back to a specific
    # websocket session (used by direct tools, pyodide code interpreter).
    if metadata.get('session_id') and metadata.get('chat_id') and metadata.get('message_id'):
        event_caller = await get_event_call(metadata)

    return event_emitter, event_caller


async def build_chat_response_context(request, form_data, user, model, metadata, tasks, events):
    event_emitter, event_caller = await get_event_emitter_and_caller(metadata)
    return {
        'request': request,
        'form_data': form_data,
        'user': user,
        'model': model,
        'metadata': metadata,
        'tasks': tasks,
        'events': events,
        'event_emitter': event_emitter,
        'event_caller': event_caller,
    }


def get_response_data(response):
    if isinstance(response, list) and len(response) == 1:
        # If the response is a single-item list, unwrap it #17213
        response = response[0]

    if isinstance(response, JSONResponse):
        if isinstance(response.body, bytes):
            try:
                response_data = JSONCodec.loads(response.body.decode('utf-8', 'replace'))
            except json.JSONDecodeError:
                response_data = {'error': {'detail': 'Invalid JSON response'}}
        else:
            response_data = response
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = None

    return response, response_data


def merge_events_into_response(response_data, events):
    if events and isinstance(events, list):
        extra_response = {}
        for event in events:
            if isinstance(event, dict):
                extra_response.update(event)
            else:
                extra_response[event] = True

        return {
            **extra_response,
            **response_data,
        }
    return response_data


def build_response_object(response, response_data):
    if isinstance(response, dict):
        return response_data
    if isinstance(response, JSONResponse):
        return JSONResponse(
            content=response_data,
            headers=response.headers,
            status_code=response.status_code,
        )
    return response


def update_assistant_message_from_stream(assistant_message, raw):
    line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
    if not isinstance(line, str):
        return

    def append_output_text(item, text):
        parts = item.setdefault('content', [])
        if parts and parts[-1].get('type') == 'output_text':
            parts[-1]['text'] += text
        else:
            parts.append({'type': 'output_text', 'text': text})

    for raw_part in line.splitlines():
        part = raw_part.removeprefix('data:').strip()
        if not part or part == '[DONE]':
            continue

        try:
            data = JSONCodec.loads(part)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        if data.get('type', '').startswith('response.'):
            output, meta = handle_responses_streaming_event(data, assistant_message.get('output', []))
            if output:
                assistant_message['output'] = output
            if meta and meta.get('usage'):
                assistant_message['usage'] = merge_usage(assistant_message.get('usage'), meta['usage'])
            continue

        raw_usage = data.get('usage', {}) or {}
        raw_usage.update(data.get('timings', {}))
        if raw_usage:
            assistant_message['usage'] = merge_usage(assistant_message.get('usage'), raw_usage)

        for choice in data.get('choices', []):
            delta = choice.get('delta', {}) or {}
            content = delta.get('content')
            reasoning_content = delta.get('reasoning_content') or delta.get('reasoning') or delta.get('thinking')

            if reasoning_content:
                output = assistant_message.setdefault('output', [])
                if not output or output[-1].get('type') != 'reasoning':
                    output.append(
                        {
                            'type': 'reasoning',
                            'id': output_id('r'),
                            'status': 'in_progress',
                            'start_tag': '<think>',
                            'end_tag': '</think>',
                            'attributes': {'type': 'reasoning_content'},
                            'content': [],
                            'summary': None,
                            'started_at': time.time(),
                        }
                    )

                append_output_text(output[-1], reasoning_content)

            if content:
                output = assistant_message.get('output')
                if output:
                    if output[-1].get('type') == 'reasoning':
                        output[-1]['status'] = 'completed'
                        output[-1]['ended_at'] = time.time()
                        output[-1]['duration'] = int(output[-1]['ended_at'] - output[-1]['started_at'])

                    if not output or output[-1].get('type') != 'message':
                        output.append(
                            {
                                'type': 'message',
                                'id': output_id('msg'),
                                'status': 'in_progress',
                                'role': 'assistant',
                                'content': [],
                            }
                        )

                    append_output_text(output[-1], content)

                assistant_message['content'] = assistant_message.get('content', '') + content


async def get_system_oauth_token(request, user):
    """Get the system OAuth token for a user.

    Primary path: use the oauth_session_id cookie (browser requests).
    Fallback: look up the user's most recent OAuth session from the DB
    (covers automations, API calls, and other cookie-less contexts).
    """
    oauth_token = None
    try:
        oauth_session_id = request.cookies.get('oauth_session_id', None)
        if oauth_session_id:
            oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                user.id,
                oauth_session_id,
            )

        # Fallback: no cookie (automation, API key, etc.) — use most recent session
        if oauth_token is None:
            from open_webui.models.oauth_sessions import OAuthSessions

            sessions = await OAuthSessions.get_sessions_by_user_id(user.id)
            # Filter out MCP-provider sessions — their token refresh is handled
            # separately by oauth_client_manager.  Passing them to the SSO
            # oauth_manager causes a failed refresh and session deletion (#24618).
            sessions = [s for s in sessions if not (s.provider or '').startswith('mcp:')]
            if sessions:
                best = max(sessions, key=lambda s: s.updated_at)
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    best.id,
                )
    except Exception as e:
        log.error(f'Error getting OAuth token: {e}')
    return oauth_token


async def background_tasks_handler(ctx):
    request = ctx['request']
    form_data = ctx['form_data']
    user = ctx['user']
    metadata = ctx['metadata']
    tasks = ctx['tasks']
    event_emitter = ctx['event_emitter']

    # Keeper PII card bridge: emit the pipeline-provided detection summary
    # to the frontend and persist it. The list was already re-whitelisted to
    # exactly [{type,start,end}] at the trust boundary in process_chat_payload,
    # so no plaintext PII can pass through here. Mirrors the follow-ups path.
    pii_detections = (metadata or {}).get("pii_detections_public") or []
    if pii_detections:
        await event_emitter(
            {
                "type": "chat:message:pii",
                "data": {"pii_detections": pii_detections},
            }
        )
        if not metadata.get("chat_id", "").startswith("local:"):
            await Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata["chat_id"],
                metadata["message_id"],
                {"piiDetections": pii_detections},
            )
        log.info(
            "pii_card bridge: emitted chat:message:pii with %d detection(s)",
            len(pii_detections),
        )

    message = None
    messages = []

    if is_saved_chat_id(metadata.get('chat_id')):
        messages_map = await Chats.get_messages_map_by_chat_id(metadata['chat_id'])
        if not messages_map:
            # Chat was deleted while the response was streaming — skip background tasks
            return
        message = messages_map.get(metadata['message_id'])

        message_list = get_message_list(messages_map, metadata['message_id'])

        # Remove details tags and files from the messages.
        # as get_message_list creates a new list, it does not affect
        # the original messages outside of this handler

        messages = []
        for message in message_list:
            content = message.get('content', '')
            if isinstance(content, list):
                for item in content:
                    if item.get('type') == 'text':
                        content = item['text']
                        break

            if isinstance(content, str):
                content = re.sub(
                    r'<details\b[^>]*>.*?<\/details>|!\[.*?\]\(.*?\)',
                    '',
                    content,
                    flags=re.S | re.I,
                ).strip()

            messages.append(
                {
                    **message,
                    'role': message.get('role', 'assistant'),  # Safe fallback for missing role
                    'content': content,
                }
            )
    else:
        # Local temp chat, get the model and message from the form_data
        message = get_last_user_message_item(form_data.get('messages', []))
        messages = form_data.get('messages', [])
        if message:
            message['model'] = form_data.get('model')

    if message and 'model' in message:
        if tasks and messages:
            if TASKS.FOLLOW_UP_GENERATION in tasks and tasks[TASKS.FOLLOW_UP_GENERATION]:
                res = await generate_follow_ups(
                    request,
                    {
                        'model': message['model'],
                        'messages': messages,
                        'message_id': metadata['message_id'],
                        'chat_id': metadata['chat_id'],
                    },
                    user,
                )

                if res and isinstance(res, dict):
                    if len(res.get('choices', [])) == 1:
                        response_message = res.get('choices', [])[0].get('message', {})

                        follow_ups_string = response_message.get('content') or response_message.get(
                            'reasoning_content', ''
                        )
                    else:
                        follow_ups_string = ''

                    follow_ups_string = follow_ups_string[
                        follow_ups_string.find('{') : follow_ups_string.rfind('}') + 1
                    ]

                    try:
                        follow_ups = JSONCodec.loads(follow_ups_string).get('follow_ups', [])
                        await event_emitter(
                            {
                                'type': 'chat:message:follow_ups',
                                'data': {
                                    'follow_ups': follow_ups,
                                },
                            }
                        )

                        if is_saved_chat_id(metadata.get('chat_id')):
                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata['chat_id'],
                                metadata['message_id'],
                                {
                                    'followUps': follow_ups,
                                },
                                touch=False,
                            )

                    except Exception as e:
                        pass

            if is_saved_chat_id(metadata.get('chat_id')):  # Only update titles and tags for saved chats
                if TASKS.TITLE_GENERATION in tasks:
                    user_message = get_last_user_message(messages)
                    if user_message and len(user_message) > 100:
                        user_message = user_message[:100] + '...'

                    title = None
                    if tasks[TASKS.TITLE_GENERATION]:
                        res = await generate_title(
                            request,
                            {
                                'model': message['model'],
                                'messages': messages,
                                'chat_id': metadata['chat_id'],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get('choices', [])) == 1:
                                response_message = res.get('choices', [])[0].get('message', {})

                                title_string = (
                                    response_message.get('content')
                                    or response_message.get(
                                        'reasoning_content',
                                    )
                                    or message.get('content', user_message)
                                )
                            else:
                                title_string = ''

                            title_string = title_string[title_string.find('{') : title_string.rfind('}') + 1]

                            try:
                                title = JSONCodec.loads(title_string).get('title', user_message)
                            except Exception as e:
                                title = ''

                            if not title:
                                title = messages[0].get('content', user_message)

                            await Chats.update_chat_title_by_id(metadata['chat_id'], title)

                            await event_emitter(
                                {
                                    'type': 'chat:title',
                                    'data': title,
                                }
                            )

                    if title == None and len(messages) == 2 and (not messages_map or len(messages_map) <= 2):
                        title = messages[0].get('content', user_message)

                        await Chats.update_chat_title_by_id(metadata['chat_id'], title)

                        await event_emitter(
                            {
                                'type': 'chat:title',
                                'data': message.get('content', user_message),
                            }
                        )

                if TASKS.TAGS_GENERATION in tasks and tasks[TASKS.TAGS_GENERATION]:
                    res = await generate_chat_tags(
                        request,
                        {
                            'model': message['model'],
                            'messages': messages,
                            'chat_id': metadata['chat_id'],
                        },
                        user,
                    )

                    if res and isinstance(res, dict):
                        if len(res.get('choices', [])) == 1:
                            response_message = res.get('choices', [])[0].get('message', {})

                            tags_string = response_message.get('content') or response_message.get(
                                'reasoning_content', ''
                            )
                        else:
                            tags_string = ''

                        tags_string = tags_string[tags_string.find('{') : tags_string.rfind('}') + 1]

                        try:
                            tags = JSONCodec.loads(tags_string).get('tags', [])
                            await Chats.update_chat_tags_by_id(metadata['chat_id'], tags, user)

                            await event_emitter(
                                {
                                    'type': 'chat:tags',
                                    'data': tags,
                                }
                            )
                        except Exception as e:
                            pass

        if messages:
            await review_memory_after_turn(
                request=request,
                user=user,
                model=ctx['model'],
                metadata=metadata,
                form_data=form_data,
                assistant_message=ctx.get('assistant_message') or {},
                messages=messages,
            )


async def outlet_filter_handler(ctx):
    """Run outlet filters inline after chat completion.

    Replaces the separate POST /api/chat/completed round-trip.
    Persists outlet-modified content to DB and emits a chat:outlet event
    so the frontend can sync its in-memory state.

    For temp/API chats, messages are built from form_data plus ctx['assistant_message'].
    """
    request = ctx['request']
    user = ctx['user']
    model = ctx['model']
    metadata = ctx['metadata']
    event_emitter = ctx.get('event_emitter')
    event_caller = ctx.get('event_caller')

    chat_id = metadata.get('chat_id', '')
    message_id = metadata.get('message_id')

    if not chat_id and not ctx.get('assistant_message'):
        return

    if not message_id:
        message_id = output_id('msg')

    is_unsaved_chat = not is_saved_chat_id(chat_id)
    try:
        messages_map = None

        if is_unsaved_chat:
            form_messages = ctx.get('form_data', {}).get('messages', [])
            assistant_message = ctx.get('assistant_message', {})

            message_list = [
                {
                    'role': m.get('role'),
                    'content': m.get('content') or get_output_text(m.get('output')),
                }
                for m in form_messages
            ]

            if assistant_message:
                message_list.append(
                    {
                        'id': message_id,
                        'role': 'assistant',
                        **assistant_message,
                    }
                )

            if not message_list:
                return
        else:
            messages_map = await Chats.get_messages_map_by_chat_id(chat_id)
            if not messages_map:
                return

            message_list = get_message_list(messages_map, message_id)
            if not message_list:
                return

        model_id = model.get('id') if isinstance(model, dict) else model

        outlet_data = {
            'model': model_id,
            'messages': [
                {
                    'id': m.get('id'),
                    'role': m.get('role'),
                    'content': m.get('content') or get_output_text(m.get('output')),
                    'info': m.get('info'),
                    'timestamp': m.get('timestamp'),
                    # Deepcopy so in-place filter mutations do not alias messages_map's baseline
                    **({'output': copy.deepcopy(m['output'])} if m.get('output') else {}),
                    **({'usage': m['usage']} if m.get('usage') else {}),
                    **({'sources': m['sources']} if m.get('sources') else {}),
                }
                for m in message_list
            ],
            'filter_ids': metadata.get('filter_ids', []),
            'chat_id': chat_id,
            'session_id': metadata.get('session_id'),
            'id': message_id,
        }

        # Pipeline outlet filters
        models = request.app.state.MODELS
        try:
            outlet_data = await process_pipeline_outlet_filter(request, outlet_data, user, models)
        except Exception as e:
            log.debug(f'Pipeline outlet filter error: {e}')

        # Function outlet filters
        extra_params = {
            '__event_emitter__': event_emitter,
            '__event_call__': event_caller,
            '__user__': user.model_dump() if isinstance(user, UserModel) else {},
            '__metadata__': metadata,
            '__request__': request,
            '__model__': model,
        }

        if ENABLE_PLUGINS:
            filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))

            outlet_result, _ = await process_filter_functions(
                request=request,
                filter_context=None,
                filter_functions=filter_functions,
                filter_type='outlet',
                form_data=outlet_data,
                extra_params=extra_params,
            )
        else:
            outlet_result = outlet_data

        if outlet_result and outlet_result.get('messages'):
            if not is_unsaved_chat and messages_map:
                for message in outlet_result['messages']:
                    outlet_message_id = message.get('id')
                    if outlet_message_id and outlet_message_id in messages_map:
                        original_message = messages_map[outlet_message_id]
                        original_content = original_message.get('content') or get_output_text(
                            original_message.get('output')
                        )
                        message_content = message.get('content') or get_output_text(message.get('output'))
                        content_changed = original_content != message_content
                        output_changed = message.get('output') and message.get('output') != original_message.get(
                            'output'
                        )
                        if content_changed or output_changed:
                            message_update = {
                                'originalContent': original_content,
                                **({'output': message['output']} if output_changed else {}),
                            }
                            if content_changed:
                                message_update['content'] = message_content or ''
                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                chat_id,
                                outlet_message_id,
                                message_update,
                            )

            if event_emitter:
                await event_emitter(
                    {
                        'type': 'chat:outlet',
                        'data': {'messages': outlet_result['messages']},
                    }
                )
    except Exception as e:
        log.debug(f'Error running outlet filters: {e}')


async def non_streaming_chat_response_handler(response, ctx):
    request = ctx['request']

    user = ctx['user']
    metadata = ctx['metadata']
    events = ctx['events']

    event_emitter = ctx['event_emitter']

    response, response_data = get_response_data(response)
    if response_data is None:
        return response

    chat_id = metadata.get('chat_id') or ''
    save_to_chat = is_saved_chat_id(chat_id)

    if event_emitter:
        try:
            if 'error' in response_data:
                error = response_data.get('error')

                if isinstance(error, dict):
                    error = error.get('detail', error)
                else:
                    error = str(error)

                log.error('Provider returned error (non-streaming): %s', error)

                if save_to_chat:
                    await Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata['chat_id'],
                        metadata['message_id'],
                        {
                            'error': {'content': error},
                        },
                    )
                if isinstance(error, str) or isinstance(error, dict):
                    await event_emitter(
                        {
                            'type': 'chat:message:error',
                            'data': {'error': {'content': error}},
                        }
                    )

            if 'selected_model_id' in response_data and save_to_chat:
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata['chat_id'],
                    metadata['message_id'],
                    {
                        'selectedModelId': response_data['selected_model_id'],
                    },
                    touch=False,
                )

            choices = response_data.get('choices', [])
            response_output = response_data.get('output')
            content = choices[0].get('message', {}).get('content') if choices else ''

            if choices and (content or response_output):
                if content or response_output:
                    await event_emitter(
                        {
                            'type': 'chat:completion',
                            'data': response_data,
                        }
                    )

                    title = await Chats.get_chat_title_by_id(metadata['chat_id']) if save_to_chat else ''

                    # Use output from backend if provided (OR-compliant backends),
                    # otherwise generate from response content
                    if not response_output:
                        choice_message = choices[0].get('message', {})
                        reasoning_content = choice_message.get('reasoning_content') or choice_message.get('reasoning')
                        reasoning_details = choice_message.get('reasoning_details')
                        response_output = []
                        if reasoning_content or reasoning_details:
                            reasoning_item = {
                                'type': 'reasoning',
                                'id': output_id('r'),
                                'status': 'completed',
                                'start_tag': '<think>',
                                'end_tag': '</think>',
                                'attributes': {'type': 'reasoning_content'},
                                'content': (
                                    [{'type': 'output_text', 'text': reasoning_content}] if reasoning_content else []
                                ),
                                'summary': None,
                            }
                            if reasoning_details:
                                reasoning_item['reasoning_details'] = (
                                    reasoning_details if isinstance(reasoning_details, list) else [reasoning_details]
                                )
                            response_output.append(reasoning_item)
                        response_output.append(
                            {
                                'type': 'message',
                                'id': output_id('msg'),
                                'status': 'completed',
                                'role': 'assistant',
                                'content': [{'type': 'output_text', 'text': content}],
                            }
                        )

                    await event_emitter(
                        {
                            'type': 'chat:completion',
                            'data': {
                                'done': True,
                                'output': response_output,
                                'title': title,
                            },
                        }
                    )

                    # Save message in the database
                    usage = normalize_usage(response_data.get('usage', {}) or {})

                    if save_to_chat:
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata['chat_id'],
                            metadata['message_id'],
                            {
                                'done': True,
                                'role': 'assistant',
                                'output': response_output,
                                **({'usage': usage} if usage else {}),
                            },
                        )

                    await publish_chat_finished_event(request, user, metadata, title, content, response_output)

                    ctx['assistant_message'] = {
                        'content': content,
                        'output': response_output,
                        **({'usage': usage} if usage else {}),
                    }
                    await outlet_filter_handler(ctx)
                    await background_tasks_handler(ctx)

            response = build_response_object(response, merge_events_into_response(response_data, events))
        except Exception as e:
            log.debug(f'Error occurred while processing request: {e}')
            chat_id = metadata.get('chat_id')
            if getattr(request.state, 'internal', False) is not True and chat_id and is_saved_chat_id(chat_id):
                webui_url = request.app.state.config.WEBUI_URL
                await publish_event(
                    request,
                    EVENTS.CHAT_FAILED,
                    actor=user,
                    subject_id=chat_id,
                    subject_type='chat',
                    data={
                        'user_id': user.id,
                        'chat_id': chat_id,
                        'message_id': metadata.get('message_id'),
                        'model_id': metadata.get('model_id'),
                        'url': f'{webui_url}/c/{chat_id}' if webui_url else f'/c/{chat_id}',
                        'message': str(e),
                    },
                    message='Chat failed',
                )
            pass

        return response

    choices = response_data.get('choices', [])
    output = response_data.get('output')
    content = choices[0].get('message', {}).get('content') if choices else ''
    if ENABLE_API_OUTLET_FILTERS and (content or output):
        usage = normalize_usage(response_data.get('usage', {}) or {})
        ctx['assistant_message'] = {
            **({'content': content} if content else {}),
            **({'output': output} if output else {}),
            **({'usage': usage} if usage else {}),
        }
        await outlet_filter_handler(ctx)

    if isinstance(response, dict):
        response = merge_events_into_response(response_data, events)

    return response


async def streaming_chat_response_handler(response, ctx):
    request = ctx['request']

    form_data = ctx['form_data']

    user = ctx['user']
    model = ctx['model']

    metadata = ctx['metadata']
    events = ctx['events']

    event_emitter = ctx['event_emitter']
    event_caller = ctx['event_caller']
    chat_id = metadata.get('chat_id') or ''
    save_to_chat = is_saved_chat_id(chat_id)

    extra_params = {
        '__event_emitter__': event_emitter,
        '__event_call__': event_caller,
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__oauth_token__': await get_system_oauth_token(request, user),
        '__request__': request,
        '__model__': model,
    }

    filter_functions = (
        await get_filter_functions(request, model, metadata.get('filter_ids', [])) if ENABLE_PLUGINS else []
    )

    # Standard streaming response handler
    # event_caller is optional — only needed for direct (client-side) tools
    # and pyodide code interpreter. Server-side tools work without it.
    if event_emitter:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get('model', '')

        # Handle as a background task
        async def response_handler(response, events):
            filter_context = FilterContext()
            tag_scan_positions = {}

            def tag_output_handler(content_type, tags, output):
                """
                Detect special tags (reasoning, solution, code_interpreter) in streaming
                content and create corresponding OR-aligned output items directly.
                Operates on output items instead of content_blocks.

                Uses the text from the output items themselves for tag detection,
                eliminating state divergence between accumulated content and items.
                """
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:
                        return attributes
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                def get_last_text(out):
                    """Get text from last message item, or empty string."""
                    if out and out[-1].get('type') == 'message':
                        parts = out[-1].get('content', [])
                        if parts and parts[-1].get('type') == 'output_text':
                            return parts[-1].get('text', '')
                    return ''

                def set_last_text(out, text):
                    """Set text on last message item's output_text."""
                    if out and out[-1].get('type') == 'message':
                        parts = out[-1].get('content', [])
                        if parts and parts[-1].get('type') == 'output_text':
                            parts[-1]['text'] = text

                def get_scanned_length(item, text):
                    item_id = item.get('id')
                    if not item_id:
                        return 0

                    scanned_length = tag_scan_positions.get((item_id, content_type), 0)
                    return scanned_length if scanned_length <= len(text) else 0

                def save_scanned_length(item, text):
                    item_id = item.get('id')
                    if item_id:
                        tag_scan_positions[(item_id, content_type)] = len(text)

                def clear_scanned_length(item):
                    item_id = item.get('id')
                    if item_id:
                        tag_scan_positions.pop((item_id, content_type), None)

                # Map content_type to output item type
                output_type_map = {
                    'reasoning': 'reasoning',
                    'solution': 'message',  # solution tags just produce text
                    'code_interpreter': 'open_webui:code_interpreter',
                }
                output_item_type = output_type_map.get(content_type, content_type)

                last_type = output[-1].get('type', '') if output else ''

                if last_type == 'message':
                    # Use the output item's own text for tag detection
                    item = output[-1]
                    item_text = get_last_text(output)
                    scanned_length = get_scanned_length(item, item_text)
                    max_start_tag_length = max((len(start_tag) for start_tag, _ in tags), default=1)
                    search_start = max(0, scanned_length - max_start_tag_length + 1)

                    if scanned_length and any(
                        start_tag.startswith('<') and start_tag.endswith('>') for start_tag, _ in tags
                    ):
                        last_tag_boundary = max(
                            item_text.rfind('>', 0, scanned_length),
                            item_text.rfind('\n', 0, scanned_length),
                        )
                        open_tag_start = item_text.rfind('<', 0, scanned_length)
                        if open_tag_start > last_tag_boundary:
                            search_start = min(search_start, open_tag_start)

                    for start_tag, end_tag in tags:
                        match = re.compile(_start_tag_pattern(start_tag)).search(item_text, search_start)
                        if match:
                            clear_scanned_length(item)
                            try:
                                attr_content = match.group(1) if match.group(1) else ''
                            except Exception:
                                attr_content = ''

                            attributes = extract_attributes(attr_content)

                            before_tag = item_text[: match.start()]
                            after_tag = item_text[match.end() :]

                            # Keep only text before the tag in the message
                            set_last_text(output, before_tag)

                            if not before_tag.strip():
                                # Remove empty message item
                                if output and output[-1].get('type') == 'message':
                                    output.pop()

                            # Append the new output item
                            if output_item_type == 'reasoning':
                                output.append(
                                    {
                                        'type': 'reasoning',
                                        'id': output_id('r'),
                                        'status': 'in_progress',
                                        'start_tag': start_tag,
                                        'end_tag': end_tag,
                                        'attributes': attributes,
                                        'content': [],
                                        'summary': None,
                                        'started_at': time.time(),
                                    }
                                )
                            elif output_item_type == 'open_webui:code_interpreter':
                                output.append(
                                    {
                                        'type': 'open_webui:code_interpreter',
                                        'id': output_id('ci'),
                                        'status': 'in_progress',
                                        'start_tag': start_tag,
                                        'end_tag': end_tag,
                                        'attributes': attributes,
                                        'lang': attributes.get('lang', 'python'),
                                        'code': '',
                                        'output': None,
                                        'started_at': time.time(),
                                    }
                                )
                            else:
                                # solution or other text-producing tag
                                output.append(
                                    {
                                        'type': 'message',
                                        'id': output_id('msg'),
                                        'status': 'in_progress',
                                        'role': 'assistant',
                                        'content': [{'type': 'output_text', 'text': ''}],
                                        '_tag_type': content_type,
                                        'start_tag': start_tag,
                                        'end_tag': end_tag,
                                        'attributes': attributes,
                                        'started_at': time.time(),
                                    }
                                )

                            if after_tag:
                                # Set the after_tag content on the new item
                                if output_item_type == 'reasoning':
                                    output[-1]['content'] = [{'type': 'output_text', 'text': after_tag}]
                                elif output_item_type == 'open_webui:code_interpreter':
                                    output[-1]['code'] = after_tag
                                else:
                                    set_last_text(output, after_tag)

                                _, recursive_end = tag_output_handler(content_type, tags, output)
                                if recursive_end:
                                    end_flag = True

                            break
                    else:
                        save_scanned_length(item, item_text)

                elif (
                    (last_type == 'reasoning' and content_type == 'reasoning')
                    or (last_type == 'open_webui:code_interpreter' and content_type == 'code_interpreter')
                    or (last_type == 'message' and output[-1].get('_tag_type') == content_type)
                ):
                    item = output[-1]
                    start_tag = item.get('start_tag', '')
                    end_tag = item.get('end_tag', '')

                    # Get the block content from the item itself
                    if last_type == 'reasoning':
                        parts = item.get('content', [])
                        block_content = ''
                        if parts and parts[-1].get('type') == 'output_text':
                            block_content = parts[-1].get('text', '')
                    elif last_type == 'open_webui:code_interpreter':
                        block_content = item.get('code', '')
                    else:
                        block_content = get_last_text(output)

                    scanned_length = get_scanned_length(item, block_content)
                    end_tag_search_start = max(0, scanned_length - max(len(end_tag), 1) + 1)

                    if block_content.find(end_tag, end_tag_search_start) != -1:
                        clear_scanned_length(item)
                        end_flag = True

                        # Strip start and end tags from content
                        start_tag_pattern = _start_tag_pattern(start_tag)
                        block_content = re.sub(start_tag_pattern, '', block_content).strip()

                        end_tag_pattern = rf'{re.escape(end_tag)}'
                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        block_content = split_content[0].strip() if split_content else ''
                        leftover_content = split_content[1].strip() if len(split_content) > 1 else ''

                        if block_content:
                            # Update the item with final content
                            if last_type == 'reasoning':
                                item['content'] = [{'type': 'output_text', 'text': block_content}]
                                item['ended_at'] = time.time()
                                item['duration'] = int(item['ended_at'] - item['started_at'])
                                item['status'] = 'completed'
                            elif last_type == 'open_webui:code_interpreter':
                                item['code'] = block_content
                                item['ended_at'] = time.time()
                                item['duration'] = int(item['ended_at'] - item['started_at'])
                            else:
                                set_last_text(output, block_content)
                                item['ended_at'] = time.time()

                            # Reset by appending a new message item for leftover
                            output.append(
                                {
                                    'type': 'message',
                                    'id': output_id('msg'),
                                    'status': 'in_progress',
                                    'role': 'assistant',
                                    'content': [
                                        {
                                            'type': 'output_text',
                                            'text': leftover_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            # Remove the block if content is empty
                            output.pop()
                            output.append(
                                {
                                    'type': 'message',
                                    'id': output_id('msg'),
                                    'status': 'in_progress',
                                    'role': 'assistant',
                                    'content': [
                                        {
                                            'type': 'output_text',
                                            'text': leftover_content,
                                        }
                                    ],
                                }
                            )
                    else:
                        save_scanned_length(item, block_content)

                return output, end_flag

            message = (
                await Chats.get_message_by_id_and_message_id(metadata['chat_id'], metadata['message_id'])
                if save_to_chat
                else None
            )

            tool_calls = []

            last_assistant_message = None
            try:
                if form_data['messages'][-1]['role'] == 'assistant':
                    last_assistant_message = get_last_assistant_message(form_data['messages'])
            except Exception as e:
                pass

            initial_content = (
                message.get('content', '') if message else last_assistant_message if last_assistant_message else ''
            )
            content_parts = [initial_content] if initial_content else []

            # Initialize output: use existing from message if continuing, else create new
            existing_output = message.get('output') if message else None
            if existing_output:
                output = existing_output
            else:
                # Only create an initial message item if there is content to initialize with
                if initial_content:
                    output = [
                        {
                            'type': 'message',
                            'id': output_id('msg'),
                            'status': 'in_progress',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': initial_content}],
                        }
                    ]
                else:
                    output = []

            usage = None
            prior_output = []
            last_response_id = None

            def full_output():
                return prior_output + output if prior_output else output

            def get_message_error_content(error):
                if isinstance(error, HTTPException):
                    error = error.detail
                elif isinstance(error, dict):
                    error = error.get('detail', error)
                else:
                    error = str(error)

                return error if isinstance(error, (str, dict)) else str(error)

            async def emit_message_error(error_content):
                if save_to_chat:
                    await Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata['chat_id'],
                        metadata['message_id'],
                        {'error': {'content': error_content}},
                    )
                await event_emitter(
                    {
                        'type': 'chat:message:error',
                        'data': {'error': {'content': error_content}},
                    }
                )

            reasoning_tags_param = metadata.get('params', {}).get('reasoning_tags')
            DETECT_REASONING_TAGS = reasoning_tags_param is not False

            # Mirror the five gates from utils/tools.py get_builtin_tools so the
            # legacy XML-tag path enforces the same authz as native FC.
            features = metadata.get('features', {}) or {}
            model_capabilities = model.get('info', {}).get('meta', {}).get('capabilities') or {}
            builtin_tools_meta = model.get('info', {}).get('meta', {}).get('builtinTools', {})
            DETECT_CODE_INTERPRETER = (
                bool(features.get('code_interpreter'))
                and builtin_tools_meta.get('code_interpreter', True)
                and request.app.state.config.ENABLE_CODE_INTERPRETER
                and model_capabilities.get('code_interpreter', True)
                and (
                    getattr(user, 'role', None) == 'admin'
                    or await has_permission(
                        getattr(user, 'id', ''),
                        'features.code_interpreter',
                        request.app.state.config.USER_PERMISSIONS,
                    )
                )
            )

            reasoning_tags = []
            if DETECT_REASONING_TAGS:
                if isinstance(reasoning_tags_param, list) and len(reasoning_tags_param) == 2:
                    reasoning_tags = [(reasoning_tags_param[0], reasoning_tags_param[1])]
                else:
                    reasoning_tags = DEFAULT_REASONING_TAGS

            try:
                for event in events:
                    await event_emitter(
                        {
                            'type': 'chat:completion',
                            'data': event,
                        }
                    )

                    # Save message in the database
                    if save_to_chat:
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata['chat_id'],
                            metadata['message_id'],
                            {
                                **event,
                            },
                        )

                async def stream_body_handler(response, form_data):
                    nonlocal content_parts
                    nonlocal usage
                    nonlocal output
                    nonlocal prior_output
                    nonlocal last_response_id

                    response_tool_calls = []

                    delta_count = 0
                    delta_chunk_size = max(
                        CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
                        int(metadata.get('params', {}).get('stream_delta_chunk_size') or 1),
                    )
                    last_delta_data = None
                    last_delta_type = None

                    async def flush_pending_delta_data(threshold: int = 0):
                        nonlocal delta_count
                        nonlocal last_delta_data
                        nonlocal last_delta_type

                        if delta_count >= threshold and last_delta_data:
                            await event_emitter(
                                {
                                    'type': 'chat:completion',
                                    'data': last_delta_data,
                                }
                            )
                            delta_count = 0
                            last_delta_data = None
                            last_delta_type = None

                    async def queue_pending_delta_data(delta_data: dict, delta_type: str):
                        nonlocal delta_count
                        nonlocal last_delta_data
                        nonlocal last_delta_type

                        if last_delta_type and last_delta_type != delta_type:
                            await flush_pending_delta_data()

                        delta_count += 1
                        last_delta_data = delta_data
                        last_delta_type = delta_type

                        if delta_count >= delta_chunk_size:
                            await flush_pending_delta_data(delta_chunk_size)

                    filter_extra_params = {'__body__': form_data, **extra_params} if filter_functions else None

                    async for line in response.body_iterator:
                        line = line.decode('utf-8', 'replace') if isinstance(line, bytes) else line
                        data = line

                        # Skip empty lines
                        if not data or data.isspace():
                            continue

                        # "data:" is the prefix for each event
                        if not data.startswith('data:'):
                            # Some upstreams return plain JSON error lines in a streaming response
                            # (without SSE `data:` prefix). Try to normalize these into standard
                            # error events so frontend and DB paths still receive them.
                            try:
                                raw_obj = JSONCodec.loads(data)
                                raw_error = raw_obj.get('error') if isinstance(raw_obj, dict) else None
                                if raw_error:
                                    if save_to_chat:
                                        try:
                                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata['chat_id'],
                                                metadata['message_id'],
                                                {
                                                    'error': {'content': raw_error},
                                                },
                                            )
                                        except Exception:
                                            pass
                                    await event_emitter({'type': 'chat:completion', 'data': {'error': raw_error}})
                            except Exception:
                                pass
                            continue

                        # Remove the "data:" prefix
                        data = data[5:].strip()

                        try:
                            data = JSONCodec.loads(data)

                            if filter_functions:
                                data, _ = await process_filter_functions(
                                    request=request,
                                    filter_context=filter_context,
                                    filter_functions=filter_functions,
                                    filter_type='stream',
                                    form_data=data,
                                    extra_params=filter_extra_params,
                                )

                            if data:
                                if 'event' in data and not getattr(request.state, 'direct', False):
                                    await event_emitter(data.get('event', {}))

                                if 'selected_model_id' in data:
                                    model_id = data['selected_model_id']
                                    if save_to_chat:
                                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                                            metadata['chat_id'],
                                            metadata['message_id'],
                                            {
                                                'selectedModelId': model_id,
                                            },
                                            touch=False,
                                        )
                                    await event_emitter(
                                        {
                                            'type': 'chat:completion',
                                            'data': data,
                                        }
                                    )
                                # Check for Responses API events (type field starts with "response.")
                                elif data.get('type', '').startswith('response.'):
                                    response_event_type = data.get('type', '')
                                    response_event_is_delta = response_event_type.endswith('.delta')
                                    output, response_metadata = handle_responses_streaming_event(data, output)

                                    if not response_event_is_delta:
                                        await flush_pending_delta_data()

                                    # Emit citation sources from finalized output items
                                    # (mirrors Chat Completions annotation handling at delta level)
                                    if response_event_type == 'response.output_item.done':
                                        item = data.get('item', {})
                                        if item.get('type') == 'message':
                                            for part in item.get('content', []):
                                                for annotation in part.get('annotations', []):
                                                    if annotation.get('type') == 'url_citation':
                                                        # Handle both flat (Responses API) and nested (Chat Completions) formats
                                                        url_citation = annotation.get('url_citation', annotation)

                                                        url = url_citation.get('url', '')
                                                        title = url_citation.get('title', url)

                                                        if url:
                                                            await event_emitter(
                                                                {
                                                                    'type': 'source',
                                                                    'data': {
                                                                        'source': {
                                                                            'name': title,
                                                                            'url': url,
                                                                        },
                                                                        'document': [title],
                                                                        'metadata': [
                                                                            {
                                                                                'source': url,
                                                                                'name': title,
                                                                            }
                                                                        ],
                                                                    },
                                                                }
                                                            )

                                    processed_data = {
                                        'output': full_output(),
                                    }

                                    # print(data)
                                    # print(processed_data)

                                    # Merge any metadata (usage, etc.)
                                    # Strip 'done' — response.completed emits
                                    # it but we may still need to execute tool
                                    # calls. The outer middleware manages the
                                    # actual completion signal.
                                    if response_metadata:
                                        if ENABLE_RESPONSES_API_STATEFUL:
                                            response_id = response_metadata.pop('response_id', None)
                                            if response_id:
                                                last_response_id = response_id

                                        # Normalize and capture usage for DB persistence
                                        if response_metadata.get('usage'):
                                            usage = merge_usage(usage, response_metadata['usage'])
                                            response_metadata['usage'] = usage

                                        processed_data.update(response_metadata)
                                        processed_data.pop('done', None)

                                    if response_event_is_delta:
                                        response_delta_type = response_event_type.split('.')[1]
                                        await queue_pending_delta_data(
                                            processed_data,
                                            'tool_call'
                                            if response_delta_type == 'function_call_arguments'
                                            else 'content',
                                        )
                                    else:
                                        await event_emitter(
                                            {
                                                'type': 'chat:completion',
                                                'data': processed_data,
                                            }
                                        )
                                    continue
                                else:
                                    choices = data.get('choices', [])

                                    # Normalize usage data to standard format
                                    raw_usage = data.get('usage', {}) or {}
                                    raw_usage.update(data.get('timings', {}))  # llama.cpp
                                    if raw_usage:
                                        usage = merge_usage(usage, raw_usage)
                                        await event_emitter(
                                            {
                                                'type': 'chat:completion',
                                                'data': {
                                                    'usage': usage,
                                                },
                                            }
                                        )
                                        try:
                                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {"usage": usage},
                                            )
                                        except Exception as e:
                                            log.warning(f"failed to persist usage: {e}")

                                    if not choices:
                                        error = data.get('error', {})
                                        if error:
                                            log.error('Provider returned error (streaming): %s', error)
                                            if save_to_chat:
                                                try:
                                                    await Chats.upsert_message_to_chat_by_id_and_message_id(
                                                        metadata['chat_id'],
                                                        metadata['message_id'],
                                                        {
                                                            'error': {'content': error},
                                                        },
                                                    )
                                                except Exception:
                                                    pass
                                            await event_emitter(
                                                {
                                                    'type': 'chat:completion',
                                                    'data': {
                                                        'error': error,
                                                    },
                                                }
                                            )
                                        continue

                                    delta = choices[0].get('delta', {})
                                    delta_type = 'content'

                                    # Handle delta annotations
                                    annotations = delta.get('annotations')
                                    if annotations:
                                        for annotation in annotations:
                                            if (
                                                annotation.get('type') == 'url_citation'
                                                and 'url_citation' in annotation
                                            ):
                                                url_citation = annotation['url_citation']

                                                url = url_citation.get('url', '')
                                                title = url_citation.get('title', url)

                                                await event_emitter(
                                                    {
                                                        'type': 'source',
                                                        'data': {
                                                            'source': {
                                                                'name': title,
                                                                'url': url,
                                                            },
                                                            'document': [title],
                                                            'metadata': [
                                                                {
                                                                    'source': url,
                                                                    'name': title,
                                                                }
                                                            ],
                                                        },
                                                    }
                                                )

                                    delta_tool_calls = delta.get('tool_calls', None)
                                    if delta_tool_calls:
                                        for delta_tool_call in delta_tool_calls:
                                            tool_call_index = delta_tool_call.get('index')

                                            if tool_call_index is not None:
                                                # Check if the tool call already exists
                                                current_response_tool_call = None
                                                for response_tool_call in response_tool_calls:
                                                    if response_tool_call.get('index') == tool_call_index:
                                                        current_response_tool_call = response_tool_call
                                                        break

                                                if current_response_tool_call is None:
                                                    # Add the new tool call
                                                    delta_tool_call.setdefault('function', {})
                                                    delta_tool_call['function'].setdefault('name', '')
                                                    delta_arguments = delta_tool_call['function'].get('arguments')
                                                    if not isinstance(delta_arguments, str):
                                                        delta_tool_call['function']['arguments'] = (
                                                            ''
                                                            if delta_arguments is None
                                                            else json.dumps(delta_arguments)
                                                        )
                                                    response_tool_calls.append(delta_tool_call)
                                                else:
                                                    # Update the existing tool call
                                                    delta_name = delta_tool_call.get('function', {}).get('name')
                                                    delta_arguments = delta_tool_call.get('function', {}).get(
                                                        'arguments'
                                                    )

                                                    if delta_name:
                                                        current_response_tool_call['function']['name'] = delta_name

                                                    if delta_arguments is not None:
                                                        if not isinstance(delta_arguments, str):
                                                            delta_arguments = json.dumps(delta_arguments)
                                                        current_response_tool_call.setdefault('function', {})
                                                        if not isinstance(
                                                            current_response_tool_call['function'].get('arguments'),
                                                            str,
                                                        ):
                                                            current_response_tool_call['function']['arguments'] = ''
                                                        current_response_tool_call['function']['arguments'] += (
                                                            delta_arguments
                                                        )

                                        # Emit pending tool calls in real-time
                                        if response_tool_calls:
                                            # Build pending function_call output items for display
                                            pending_fc_items = []
                                            for tc in response_tool_calls:
                                                call_id = tc.get('id', '')
                                                func = tc.get('function', {})
                                                pending_fc_items.append(
                                                    {
                                                        'type': 'function_call',
                                                        'id': call_id or output_id('fc'),
                                                        'call_id': call_id,
                                                        'name': func.get('name', ''),
                                                        'arguments': func.get('arguments', '{}'),
                                                        'status': 'in_progress',
                                                    }
                                                )

                                            data = {
                                                'output': full_output() + pending_fc_items,
                                            }
                                            delta_type = 'tool_call'

                                    delta_images = delta.get('images')
                                    image_urls = (
                                        await get_image_urls(delta_images, request, metadata, user)
                                        if delta_images
                                        else []
                                    )
                                    if image_urls:
                                        image_file_list = [{'type': 'image', 'url': url} for url in image_urls]
                                        message_files = image_file_list
                                        if save_to_chat:
                                            message_files = await Chats.add_message_files_by_id_and_message_id(
                                                metadata['chat_id'],
                                                metadata['message_id'],
                                                image_file_list,
                                            )
                                            if message_files is None:
                                                message_files = image_file_list

                                        await event_emitter(
                                            {
                                                'type': 'files',
                                                'data': {'files': message_files},
                                            }
                                        )

                                    value = delta.get('content')

                                    reasoning_content = (
                                        delta.get('reasoning_content')
                                        or delta.get('reasoning')
                                        or delta.get('thinking')
                                    )
                                    reasoning_details = delta.get('reasoning_details')
                                    reasoning_detail_items = (
                                        [item for item in reasoning_details if isinstance(item, dict)]
                                        if isinstance(reasoning_details, list)
                                        else [reasoning_details]
                                        if isinstance(reasoning_details, dict)
                                        else []
                                    )
                                    existing_reasoning_item = next(
                                        (item for item in reversed(output) if item.get('type') == 'reasoning'),
                                        None,
                                    )
                                    message_index = next(
                                        (i for i, item in enumerate(output) if item.get('type') == 'message'),
                                        None,
                                    )
                                    if reasoning_content or (
                                        reasoning_detail_items
                                        and (
                                            existing_reasoning_item
                                            or any(
                                                item.get('text') or item.get('summary') or item.get('data')
                                                for item in reasoning_detail_items
                                            )
                                        )
                                    ):
                                        reasoning_item = (
                                            existing_reasoning_item
                                            if (reasoning_detail_items and not reasoning_content)
                                            or message_index is not None
                                            else None
                                        )

                                        if reasoning_item is None:
                                            if not output or output[-1].get('type') != 'reasoning':
                                                reasoning_item = {
                                                    'type': 'reasoning',
                                                    'id': output_id('r'),
                                                    'status': 'in_progress',
                                                    'start_tag': '<think>',
                                                    'end_tag': '</think>',
                                                    'attributes': {'type': 'reasoning_content'},
                                                    'content': [],
                                                    'summary': None,
                                                    'started_at': time.time(),
                                                }
                                                if message_index is not None:
                                                    reasoning_item['ended_at'] = time.time()
                                                    reasoning_item['duration'] = 0
                                                    reasoning_item['status'] = 'completed'
                                                    output.insert(message_index, reasoning_item)
                                                else:
                                                    output.append(reasoning_item)
                                            else:
                                                reasoning_item = output[-1]

                                        if reasoning_content:
                                            # Append to reasoning content
                                            parts = reasoning_item.get('content', [])
                                            if parts and parts[-1].get('type') == 'output_text':
                                                parts[-1]['text'] += reasoning_content
                                            else:
                                                reasoning_item['content'] = [
                                                    {
                                                        'type': 'output_text',
                                                        'text': reasoning_content,
                                                    }
                                                ]

                                            data = {
                                                'output': full_output(),
                                            }
                                            delta_type = 'content'

                                        if reasoning_detail_items:
                                            merge_streamed_reasoning_details(
                                                reasoning_item.setdefault('reasoning_details', []),
                                                reasoning_detail_items,
                                            )
                                            data = {
                                                'output': full_output(),
                                            }
                                            delta_type = 'content'

                                    if value:
                                        if (
                                            output
                                            and output[-1].get('type') == 'reasoning'
                                            and output[-1].get('attributes', {}).get('type') == 'reasoning_content'
                                        ):
                                            reasoning_item = output[-1]
                                            reasoning_item['ended_at'] = time.time()
                                            reasoning_item['duration'] = int(
                                                reasoning_item['ended_at'] - reasoning_item['started_at']
                                            )
                                            reasoning_item['status'] = 'completed'

                                            output.append(
                                                {
                                                    'type': 'message',
                                                    'id': output_id('msg'),
                                                    'status': 'in_progress',
                                                    'role': 'assistant',
                                                    'content': [
                                                        {
                                                            'type': 'output_text',
                                                            'text': '',
                                                        }
                                                    ],
                                                }
                                            )

                                        if ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION:
                                            value = await convert_markdown_base64_images(
                                                request,
                                                value,
                                                {
                                                    'chat_id': metadata.get('chat_id', None),
                                                    'message_id': metadata.get('message_id', None),
                                                },
                                                user,
                                            )

                                        # closure-cell str += recopies per chunk; append + join once at read is O(n)
                                        content_parts.append(value if isinstance(value, str) else f'{value}')

                                        # Check if we're inside a tag-based block
                                        # (reasoning, code_interpreter, or solution).
                                        # If so, append to the existing in-progress
                                        # item instead of creating a new message —
                                        # otherwise tag_output_handler re-detects the
                                        # start tag on every chunk and fragments the
                                        # output.
                                        last_item = output[-1] if output else None
                                        last_item_type = last_item.get('type', '') if last_item else ''
                                        inside_tag_block = (
                                            last_item is not None
                                            and last_item.get('status') == 'in_progress'
                                            and last_item.get('attributes', {}).get('type') != 'reasoning_content'
                                            and (
                                                last_item_type == 'reasoning'
                                                or last_item_type == 'open_webui:code_interpreter'
                                                or (
                                                    last_item_type == 'message'
                                                    and last_item.get('_tag_type') is not None
                                                )
                                            )
                                        )

                                        if inside_tag_block:
                                            # Append to the existing tag-based item
                                            if last_item_type == 'open_webui:code_interpreter':
                                                last_item['code'] = last_item.get('code', '') + value
                                            elif last_item_type == 'reasoning':
                                                parts = last_item.get('content', [])
                                                if parts and parts[-1].get('type') == 'output_text':
                                                    parts[-1]['text'] += value
                                                else:
                                                    last_item['content'] = [
                                                        {
                                                            'type': 'output_text',
                                                            'text': value,
                                                        }
                                                    ]
                                            else:
                                                # solution or other _tag_type message
                                                msg_parts = last_item.get('content', [])
                                                if msg_parts and msg_parts[-1].get('type') == 'output_text':
                                                    msg_parts[-1]['text'] += value
                                                else:
                                                    last_item['content'] = [
                                                        {
                                                            'type': 'output_text',
                                                            'text': value,
                                                        }
                                                    ]
                                        else:
                                            if not output or output[-1].get('type') != 'message':
                                                output.append(
                                                    {
                                                        'type': 'message',
                                                        'id': output_id('msg'),
                                                        'status': 'in_progress',
                                                        'role': 'assistant',
                                                        'content': [
                                                            {
                                                                'type': 'output_text',
                                                                'text': '',
                                                            }
                                                        ],
                                                    }
                                                )

                                            # Append value to last message item's text
                                            msg_parts = output[-1].get('content', [])
                                            if msg_parts and msg_parts[-1].get('type') == 'output_text':
                                                msg_parts[-1]['text'] += value
                                            else:
                                                output[-1]['content'] = [
                                                    {
                                                        'type': 'output_text',
                                                        'text': value,
                                                    }
                                                ]

                                        if DETECT_REASONING_TAGS:
                                            output, _ = tag_output_handler(
                                                'reasoning',
                                                reasoning_tags,
                                                output,
                                            )

                                            output, _ = tag_output_handler(
                                                'solution',
                                                DEFAULT_SOLUTION_TAGS,
                                                output,
                                            )

                                        if DETECT_CODE_INTERPRETER:
                                            output, end = tag_output_handler(
                                                'code_interpreter',
                                                DEFAULT_CODE_INTERPRETER_TAGS,
                                                output,
                                            )

                                            if end:
                                                break

                                        if ENABLE_REALTIME_CHAT_SAVE and save_to_chat:
                                            current_output = full_output()
                                            # Save message in the database
                                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata['chat_id'],
                                                metadata['message_id'],
                                                {
                                                    'output': current_output,
                                                },
                                            )
                                            data = {
                                                'output': current_output,
                                            }
                                            delta_type = 'content'
                                        else:
                                            data = {
                                                'output': full_output(),
                                            }
                                            delta_type = 'content'

                                if delta:
                                    await queue_pending_delta_data(data, delta_type)
                                else:
                                    await event_emitter(
                                        {
                                            'type': 'chat:completion',
                                            'data': data,
                                        }
                                    )
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            raise
                        except Exception as e:
                            done = 'data: [DONE]' in line
                            if done:
                                pass
                            else:
                                log.debug(f'Error: {e}')
                                continue
                    await flush_pending_delta_data()

                    if output:
                        # Clean up the last message item
                        if output[-1].get('type') == 'message':
                            parts = output[-1].get('content', [])
                            if parts and parts[-1].get('type') == 'output_text':
                                parts[-1]['text'] = parts[-1]['text'].strip()

                                if not parts[-1]['text']:
                                    output.pop()

                                    if not output:
                                        output.append(
                                            {
                                                'type': 'message',
                                                'id': output_id('msg'),
                                                'status': 'in_progress',
                                                'role': 'assistant',
                                                'content': [{'type': 'output_text', 'text': ''}],
                                            }
                                        )

                        if output[-1].get('type') == 'reasoning':
                            reasoning_item = output[-1]
                            if reasoning_item.get('ended_at') is None:
                                reasoning_item['ended_at'] = time.time()
                                reasoning_item['duration'] = int(
                                    reasoning_item['ended_at'] - reasoning_item['started_at']
                                )
                                reasoning_item['status'] = 'completed'

                    if response_tool_calls:
                        tool_calls.append(_split_tool_calls(response_tool_calls))

                    # Responses API path: extract function_call items from output
                    if not response_tool_calls and output:
                        # Collect call_ids that already have results,
                        # including those from prior_output so we don't
                        # re-process tool calls from a previous turn.
                        handled_call_ids = {
                            item.get('call_id')
                            for item in (prior_output + output)
                            if item.get('type') == 'function_call_output'
                        }
                        responses_api_tool_calls = []
                        for item in output:
                            if item.get('type') == 'function_call' and item.get('call_id') not in handled_call_ids:
                                arguments = item.get('arguments', '{}')
                                responses_api_tool_calls.append(
                                    {
                                        'id': item.get('call_id', ''),
                                        'index': len(responses_api_tool_calls),
                                        'function': {
                                            'name': item.get('name', ''),
                                            'arguments': (
                                                arguments if isinstance(arguments, str) else json.dumps(arguments)
                                            ),
                                        },
                                    }
                                )
                        if responses_api_tool_calls:
                            tool_calls.append(_split_tool_calls(responses_api_tool_calls))

                try:
                    try:
                        await stream_body_handler(response, form_data)
                    except asyncio.TimeoutError:
                        _timeout_error = (
                            "Stream timed out — the model stopped responding. "
                            "Please try again."
                        )
                        log.warning(
                            f"[stream] Upstream idle timeout (sock_read) after "
                            f"{AIOHTTP_CLIENT_TIMEOUT_SOCK_READ}s with no chunks: "
                            f"chat_id={metadata.get('chat_id')} model={model_id}"
                        )
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata["chat_id"],
                            metadata["message_id"],
                            {"error": {"content": _timeout_error}},
                        )
                        await event_emitter(
                            {
                                "type": "chat:message:error",
                                "data": {"error": {"content": _timeout_error}},
                            }
                        )
                finally:
                    if response.background:
                        await response.background()

                tool_call_iterations = 0
                max_tool_call_iterations = getattr(
                    request.state,
                    'max_tool_call_iterations',
                    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS,
                )
                tool_call_sources = []  # Track citation sources from tool results
                all_tool_call_sources = []  # Accumulated sources across all iterations
                user_message = get_last_user_message(form_data['messages'])

                # Check if citations are enabled for this model
                citations_enabled = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get(
                    'citations', True
                )

                # Use the pre-RAG system content captured before the
                # initial file-source injection in process_chat_payload.
                # This ensures restore truly undoes the RAG template.
                original_system_content = metadata.get('system_prompt')
                if original_system_content is None:
                    original_system_message = get_system_message(form_data['messages'])
                    original_system_content = (
                        get_content_from_message(original_system_message) if original_system_message else None
                    )

                while tool_calls and (
                    max_tool_call_iterations is None or tool_call_iterations < max_tool_call_iterations
                ):
                    tool_call_iterations += 1

                    response_tool_calls = tool_calls.pop(0)

                    # Append function_call items for each tool call
                    # (Responses API already has them from streaming, so skip duplicates)
                    existing_call_ids = {item.get('call_id') for item in output if item.get('type') == 'function_call'}
                    for tc in response_tool_calls:
                        call_id = tc.get('id', '')
                        if call_id not in existing_call_ids:
                            func = tc.get('function', {})
                            output.append(
                                {
                                    'type': 'function_call',
                                    'id': call_id or output_id('fc'),
                                    'call_id': call_id,
                                    'name': func.get('name', ''),
                                    'arguments': func.get('arguments', '{}'),
                                    'status': 'in_progress',
                                }
                            )

                    await event_emitter(
                        {
                            'type': 'chat:completion',
                            'data': {
                                'output': full_output(),
                            },
                        }
                    )

                    tools = metadata.get('tools', {})

                    results = []

                    def parse_tool_params(tool_call):
                        tool_args = tool_call.get('function', {}).get('arguments', '{}')
                        params = {}
                        if tool_args and tool_args.strip():
                            try:
                                params = JSONCodec.loads(tool_args)
                            except Exception:
                                try:
                                    params = ast.literal_eval(tool_args)
                                except Exception as e:
                                    log.debug(e)
                                    return None
                        tool_call.setdefault('function', {})['arguments'] = json.dumps(params)
                        return params

                    async def execute_tool_call(tool_call):
                        name = tool_call.get('function', {}).get('name', '')
                        params = parse_tool_params(tool_call)
                        if params is None:
                            return {}, None, None, None, False
                        tool = tools.get(name)
                        if not tool:
                            return params, f'Error: Tool "{name}" not found.', None, None, False
                        spec = tool.get('spec', {})
                        tool_type = tool.get('type', '')
                        direct_tool = tool.get('direct', False)
                        allowed_params = spec.get('parameters', {}).get('properties', {}).keys()
                        params = {key: value for key, value in params.items() if key in allowed_params}
                        try:
                            if direct_tool:
                                result = await event_caller(
                                    {
                                        'type': 'execute:tool',
                                        'data': {
                                            'id': str(uuid4()),
                                            'name': name,
                                            'params': params,
                                            'server': tool.get('server', {}),
                                            'session_id': metadata.get('session_id'),
                                        },
                                    }
                                )
                            else:
                                function = await get_updated_tool_function(
                                    function=tool['callable'],
                                    extra_params={
                                        '__messages__': form_data.get('messages', []),
                                        '__files__': metadata.get('files', []),
                                    },
                                )
                                result = await function(**params)
                        except Exception as e:
                            result = str(e)
                        return params, result, tool, tool_type, direct_tool

                    delegate_calls = [
                        tool_call
                        for tool_call in response_tool_calls
                        if tool_call.get('function', {}).get('name') == 'delegate_task'
                    ]
                    tool_results = {}
                    for tool_call in response_tool_calls:
                        if tool_call.get('function', {}).get('name') != 'delegate_task':
                            tool_results[id(tool_call)] = await execute_tool_call(tool_call)
                    tool_results.update(
                        zip(
                            [id(tool_call) for tool_call in delegate_calls],
                            await asyncio.gather(*(execute_tool_call(tool_call) for tool_call in delegate_calls)),
                        )
                    )

                    for tool_call in response_tool_calls:
                        tool_call_id = tool_call.get('id', '')
                        tool_function_name = tool_call.get('function', {}).get('name', '')
                        tool_function_params, tool_result, tool, tool_type, direct_tool = tool_results[id(tool_call)]
                        if tool_result is None:
                            results.append(
                                {
                                    'tool_call_id': tool_call_id,
                                    'content': (
                                        'Error: Tool call arguments could not be parsed. The model generated '
                                        f'malformed or incomplete JSON for `{tool_function_name}`. Please try again.'
                                    ),
                                }
                            )
                            continue

                        tool_result, tool_result_files, tool_result_embeds = await process_tool_result(
                            request,
                            tool_function_name,
                            tool_result,
                            tool_type,
                            direct_tool,
                            metadata,
                            user,
                        )

                        await terminal_event_handler(
                            tool_function_name,
                            tool_function_params,
                            tool_result,
                            event_emitter,
                        )

                        # Extract citation sources from tool results
                        if (
                            citations_enabled
                            and tool_function_name
                            in [
                                'search_web',
                                'fetch_url',
                                'view_file',
                                'view_knowledge_file',
                                'query_knowledge_files',
                                'query_chat_files',
                            ]
                            and tool_result
                        ):
                            try:
                                citation_sources = get_citation_source_from_tool_result(
                                    tool_name=tool_function_name,
                                    tool_params=tool_function_params,
                                    tool_result=tool_result,
                                    tool_id=tool.get('tool_id', '') if tool else '',
                                )
                                tool_call_sources.extend(citation_sources)
                            except Exception as e:
                                log.exception(f'Error extracting citation source: {e}')

                        results.append(
                            {
                                'tool_call_id': tool_call_id,
                                'content': str(tool_result) if tool_result else '',
                                **({'files': tool_result_files} if tool_result_files else {}),
                                **({'embeds': tool_result_embeds} if tool_result_embeds else {}),
                            }
                        )

                    # Update function_call statuses and append function_call_output items
                    for tc in response_tool_calls:
                        call_id = tc.get('id', '')
                        # Mark function_call as completed
                        for item in output:
                            if item.get('type') == 'function_call' and item.get('call_id') == call_id:
                                item['status'] = 'completed'
                                # Update arguments with parsed/sanitized version
                                item['arguments'] = tc.get('function', {}).get('arguments', '{}')
                                break

                    for result in results:
                        output_parts = [{'type': 'input_text', 'text': result.get('content', '')}]

                        # Separate image data URIs (for LLM via input_image) from
                        # other files (for frontend display via files attribute).
                        display_files = []
                        for file_item in result.get('files', []):
                            if file_item.get('type') == 'image' and file_item.get('url', '').startswith('data:'):
                                # LLM-only: add as input_image part, not frontend display output.
                                output_parts.append({'type': 'input_image', 'image_url': file_item['url']})
                            else:
                                # Frontend display (MCP images, audio, etc.)
                                display_files.append(file_item)

                        output.append(
                            {
                                'type': 'function_call_output',
                                'id': output_id('fco'),
                                'call_id': result.get('tool_call_id', ''),
                                'output': output_parts,
                                'status': 'completed',
                                **({'files': display_files} if display_files else {}),
                                **({'embeds': result.get('embeds')} if result.get('embeds') else {}),
                            }
                        )

                    # Append a new empty message item for the next response
                    output.append(
                        {
                            'type': 'message',
                            'id': output_id('msg'),
                            'status': 'in_progress',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': ''}],
                        }
                    )

                    # Emit citation sources to the frontend for display
                    if citations_enabled:
                        for source in tool_call_sources:
                            await event_emitter({'type': 'source', 'data': source})

                        # Apply tool source context to messages for the model.
                        # Restoring to pre-RAG original prevents duplicating
                        # the RAG template across file and tool sources.
                        all_tool_call_sources.extend(tool_call_sources)
                        if all_tool_call_sources and user_message:
                            # Restore pre-RAG message state before re-applying
                            # to prevent RAG template duplication.
                            original_user_message = metadata.get('user_prompt') or user_message
                            set_last_user_message_content(
                                original_user_message,
                                form_data['messages'],
                            )
                            if original_system_content is not None:
                                if get_system_message(form_data['messages']):
                                    replace_system_message_content(
                                        original_system_content,
                                        form_data['messages'],
                                    )
                                else:
                                    form_data['messages'] = add_or_update_system_message(
                                        original_system_content,
                                        form_data['messages'],
                                    )
                            else:
                                replace_system_message_content('', form_data['messages'])

                            # Build context: file sources with content,
                            # tool sources as citation markers only.
                            # File sources come from metadata['masked_sources'] —
                            # the PII-masked mirror — so this re-render can never
                            # put unmasked file text back in front of the LLM.
                            source_ids = {}
                            source_context = get_source_context(
                                metadata.get('masked_sources', []), source_ids
                            ) + get_source_context(
                                all_tool_call_sources,
                                source_ids,
                                include_content=False,
                            )
                            source_context = source_context.strip()
                            if source_context:
                                rag_content = await rag_template(
                                    request.app.state.config.RAG_TEMPLATE,
                                    source_context,
                                    user_message,
                                )
                                if RAG_SYSTEM_CONTEXT:
                                    form_data['messages'] = add_or_update_system_message(
                                        rag_content,
                                        form_data['messages'],
                                        append=True,
                                    )
                                else:
                                    form_data['messages'] = add_or_update_user_message(
                                        rag_content,
                                        form_data['messages'],
                                        append=False,
                                    )
                        tool_call_sources.clear()

                    # Strip input_image parts (large base64 data URIs) from the
                    # output sent to the frontend — they're only for LLM consumption
                    # via convert_output_to_messages.
                    frontend_output = []
                    for item in output:
                        if item.get('type') == 'function_call_output':
                            parts = item.get('output', [])
                            if any(p.get('type') == 'input_image' for p in parts):
                                item = {**item, 'output': [p for p in parts if p.get('type') != 'input_image']}
                        frontend_output.append(item)

                    await event_emitter(
                        {
                            'type': 'chat:completion',
                            'data': {
                                'output': frontend_output,
                            },
                        }
                    )

                    try:
                        new_form_data = {
                            **form_data,
                            'model': model_id,
                            'stream': True,
                            'metadata': metadata,
                        }

                        if ENABLE_RESPONSES_API_STATEFUL and last_response_id:
                            system_message = get_system_message(form_data['messages'])
                            new_form_data['messages'] = (
                                [system_message] if system_message else []
                            ) + convert_output_to_messages(
                                output, raw=True, reasoning_format=get_reasoning_format(model)
                            )
                            new_form_data['previous_response_id'] = last_response_id
                        else:
                            tool_messages = convert_output_to_messages(
                                output,
                                raw=True,
                                reasoning_format=get_reasoning_format(model),
                                flatten_tool_images=True,
                            )

                            # Chat Completions providers don't support multimodal
                            # tool messages.  Extract images into a user message.
                            image_urls = []
                            for message in tool_messages:
                                if message.get('role') == 'tool' and isinstance(message.get('content'), list):
                                    text_parts = []
                                    for part in message['content']:
                                        if part.get('type') == 'input_text':
                                            text_parts.append(part.get('text', ''))
                                        elif part.get('type') == 'input_image':
                                            image_urls.append(part.get('image_url', ''))
                                    message['content'] = ''.join(text_parts)

                            new_form_data['messages'] = [
                                *form_data['messages'],
                                *tool_messages,
                            ]

                            if image_urls:
                                new_form_data['messages'].append(
                                    {
                                        'role': 'user',
                                        'content': [
                                            {
                                                'type': 'text',
                                                'text': 'Here are the images from the tool results above. Please analyze them.',
                                            },
                                            *[{'type': 'image_url', 'image_url': {'url': url}} for url in image_urls],
                                        ],
                                    }
                                )

                        res = await generate_chat_completion(
                            request,
                            new_form_data,
                            user,
                            bypass_system_prompt=True,
                        )

                        if isinstance(res, StreamingResponse):
                            # Save accumulated output and start fresh.
                            # Responses API output_index values are relative
                            # to the current response — a clean output list
                            # keeps indices aligned. The display prefix
                            # ensures the UI shows tool history during
                            # streaming.
                            prior_output = list(output)
                            # Trim the trailing empty placeholder message
                            # so it doesn't persist as a ghost item once
                            # the new stream produces real content.
                            if (
                                prior_output
                                and prior_output[-1].get('type') == 'message'
                                and prior_output[-1].get('status') == 'in_progress'
                            ):
                                msg_parts = prior_output[-1].get('content', [])
                                if not msg_parts or (len(msg_parts) == 1 and not msg_parts[0].get('text', '').strip()):
                                    prior_output.pop()
                            output = []
                            await stream_body_handler(res, new_form_data)
                            output[:0] = prior_output
                            prior_output = []
                        else:
                            break
                    except Exception as e:
                        error_content = get_message_error_content(e)
                        log.exception('Tool-call continuation failed: %s', error_content)
                        await emit_message_error(error_content)
                        break

                if (
                    max_tool_call_iterations is not None
                    and tool_calls
                    and tool_call_iterations >= max_tool_call_iterations
                ):
                    log.warning('Tool-call iteration limit reached (%s)', max_tool_call_iterations)
                    error_content = f'Tool-call limit reached ({max_tool_call_iterations} iterations).'
                    await emit_message_error(error_content)

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while output and output[-1].get('type') == 'open_webui:code_interpreter' and retries < MAX_RETRIES:
                        await event_emitter(
                            {
                                'type': 'chat:completion',
                                'data': {
                                    'output': output,
                                },
                            }
                        )

                        retries += 1
                        log.debug(f'Attempt count: {retries}')

                        ci_item = output[-1]
                        ci_output = ''
                        try:
                            if ci_item.get('attributes', {}).get('type') == 'code':
                                code = ci_item.get('code', '')
                                # Sanitize code (strips ANSI codes and markdown fences)
                                code = sanitize_code(code)

                                if CODE_INTERPRETER_BLOCKED_MODULES:
                                    blocking_code = textwrap.dedent(f"""
                                        import builtins
    
                                        BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}
    
                                        _real_import = builtins.__import__
                                        async def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                                            if name.split('.')[0] in BLOCKED_MODULES:
                                                importer_name = globals.get('__name__') if globals else None
                                                if importer_name == '__main__':
                                                    raise ImportError(
                                                        f"Direct import of module {{name}} is restricted."
                                                    )
                                            return _real_import(name, globals, locals, fromlist, level)
    
                                        builtins.__import__ = restricted_import
                                    """)
                                    code = blocking_code + '\n' + code

                                ci_engine = request.app.state.config.CODE_INTERPRETER_ENGINE
                                if ci_engine == 'pyodide':
                                    ci_output = await event_caller(
                                        {
                                            'type': 'execute:python',
                                            'data': {
                                                'id': str(uuid4()),
                                                'code': code,
                                                'session_id': metadata.get('session_id', None),
                                                'files': metadata.get('files', []),
                                            },
                                        }
                                    )
                                elif ci_engine == 'jupyter':
                                    ci_output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH == 'token'
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH == 'password'
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    ci_output = {'stdout': 'Code interpreter engine not configured.'}

                                log.debug(f'Code interpreter output: {ci_output}')

                                # Handle error responses from event_caller
                                # (e.g. session disconnected, timeout)
                                if isinstance(ci_output, dict) and ci_output.get('error'):
                                    ci_output = {'stderr': ci_output['error']}

                                if isinstance(ci_output, dict):
                                    stdout = ci_output.get('stdout', '')

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split('\n')
                                        for idx, line in enumerate(stdoutLines):
                                            if re.match(r'data:image/\w+;base64', line):
                                                image_url = await get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                if image_url:
                                                    stdoutLines[idx] = f'![Output Image]({image_url})'

                                        ci_output['stdout'] = '\n'.join(stdoutLines)

                                    result = ci_output.get('result', '')

                                    if isinstance(result, str):
                                        resultLines = result.split('\n')
                                        for idx, line in enumerate(resultLines):
                                            if re.match(r'data:image/\w+;base64', line):
                                                image_url = await get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                resultLines[idx] = f'![Output Image]({image_url})'
                                        ci_output['result'] = '\n'.join(resultLines)
                        except Exception as e:
                            ci_output = str(e)

                        ci_item['output'] = ci_output
                        ci_item['status'] = 'completed'

                        output.append(
                            {
                                'type': 'message',
                                'id': output_id('msg'),
                                'status': 'in_progress',
                                'role': 'assistant',
                                'content': [{'type': 'output_text', 'text': ''}],
                            }
                        )

                        await event_emitter(
                            {
                                'type': 'chat:completion',
                                'data': {
                                    'output': output,
                                },
                            }
                        )

                        try:
                            new_form_data = {
                                **form_data,
                                'model': model_id,
                                'stream': True,
                                'metadata': metadata,
                                'messages': [
                                    *form_data['messages'],
                                    *convert_output_to_messages(
                                        output,
                                        raw=True,
                                        reasoning_format=get_reasoning_format(model),
                                        flatten_tool_images=True,
                                    ),
                                ],
                            }

                            res = await generate_chat_completion(
                                request,
                                new_form_data,
                                user,
                                bypass_system_prompt=True,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res, new_form_data)
                            else:
                                break
                        except Exception as e:
                            error_content = get_message_error_content(e)
                            log.exception('Code interpreter continuation failed: %s', error_content)
                            await emit_message_error(error_content)
                            break

                # Mark all in-progress items as completed
                for item in output:
                    if item.get('status') == 'in_progress':
                        item['status'] = 'completed'

                title = await Chats.get_chat_title_by_id(metadata['chat_id']) if save_to_chat else ''
                data = {
                    'done': True,
                    'output': output,
                    'title': title,
                    **({'usage': usage} if usage else {}),
                }

                if save_to_chat:
                    if not ENABLE_REALTIME_CHAT_SAVE:
                        # Save message in the database
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata['chat_id'],
                            metadata['message_id'],
                            {
                                'done': True,
                                'output': output,
                                **({'usage': usage} if usage else {}),
                            },
                        )
                    elif usage:
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata['chat_id'],
                            metadata['message_id'],
                            {'done': True, 'usage': usage},
                        )
                    else:
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata['chat_id'],
                            metadata['message_id'],
                            {'done': True},
                        )

                # Send a webhook notification if the user is not active
                _webhook_content = ''.join(content_parts) or get_output_text(output)
                if request.app.state.config.ENABLE_USER_WEBHOOKS and not await Users.is_user_active(user.id):
                    webhook_url = await Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{_webhook_content}\n\n{title} - {request.app.state.WEBUI_URL}/c/{metadata['chat_id']}",
                            {
                                "action": "chat",
                                "message": _webhook_content,
                                "title": title,
                                "url": f"{request.app.state.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await publish_chat_finished_event(request, user, metadata, title, ''.join(content_parts), output)

                await event_emitter(
                    {
                        'type': 'chat:completion',
                        'data': data,
                    }
                )

                ctx['assistant_message'] = {
                    'content': ''.join(content_parts) or get_output_text(output),
                    'output': output,
                    **({'usage': usage} if usage else {}),
                }
                await outlet_filter_handler(ctx)
                await background_tasks_handler(ctx)
            except asyncio.CancelledError:
                log.warning('Task was cancelled!')

                # Close the response body iterator to trigger cleanup
                # in stream_wrapper's finally block and release the
                # upstream connection.  Without this, the async
                # generator is orphaned and may spin in anyio internals.
                if hasattr(response, 'body_iterator') and hasattr(response.body_iterator, 'aclose'):
                    try:
                        await asyncio.shield(response.body_iterator.aclose())
                    except (asyncio.CancelledError, Exception):
                        pass

                async def save_cancelled_state():
                    await event_emitter({'type': 'chat:tasks:cancel'})
                    if save_to_chat:
                        if not ENABLE_REALTIME_CHAT_SAVE:
                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata['chat_id'],
                                metadata['message_id'],
                                {
                                    'done': True,
                                    'output': output,
                                },
                            )
                        else:
                            await Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata['chat_id'],
                                metadata['message_id'],
                                {'done': True},
                                touch=False,
                            )

                try:
                    await asyncio.shield(save_cancelled_state())
                except (asyncio.CancelledError, Exception):
                    pass
                raise  # re-raise CancelledError for proper propagation

            if response.background is not None:
                await response.background()

        return await response_handler(response, events)

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f'data: {item}\n\n'

            assistant_message = {}
            filter_context = FilterContext()
            has_api_outlet_filters = ENABLE_API_OUTLET_FILTERS and bool(filter_functions)
            if ENABLE_API_OUTLET_FILTERS and not has_api_outlet_filters:
                try:
                    model_id = model.get('id') if isinstance(model, dict) else model
                    has_api_outlet_filters = bool(
                        (isinstance(model, dict) and 'pipeline' in model)
                        or get_sorted_filters(model_id, request.app.state.MODELS)
                    )
                except Exception:
                    has_api_outlet_filters = True

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_context=filter_context,
                    filter_functions=filter_functions,
                    filter_type='stream',
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(JSONCodec.dumps(event))

            _source = (
                _keepalive_iter(original_generator, SSE_KEEPALIVE_INTERVAL)
                if SSE_KEEPALIVE_INTERVAL is not None
                else original_generator
            )
            async for data in _source:
                if data is _KEEPALIVE:
                    yield ": keepalive\n\n"
                    continue

                data, _ = await process_filter_functions(
                    request=request,
                    filter_context=filter_context,
                    filter_functions=filter_functions,
                    filter_type='stream',
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    if has_api_outlet_filters:
                        update_assistant_message_from_stream(assistant_message, data)
                    yield data

            if has_api_outlet_filters and assistant_message:
                ctx['assistant_message'] = assistant_message
                await outlet_filter_handler(ctx)

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )


async def process_chat_response(response, ctx):
    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        return await non_streaming_chat_response_handler(response, ctx)

    # Non standard response
    if not any(
        content_type in response.headers['Content-Type']
        for content_type in ['text/event-stream', 'application/x-ndjson']
    ):
        return response

    # Streaming response
    return await streaming_chat_response_handler(response, ctx)
