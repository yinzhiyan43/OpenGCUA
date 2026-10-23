"""Opt-in test of ONLY the repository-owned Chrome editor regression page.

Open browser_editors.html in a separate Chrome window before running this script.
The exact fixture marker is required before input; the test window is closed and
MCP released even if a comparison fails. No user webpage is an eligible target.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TITLE = 'OpenGCUA MCP editor regression'
APP = 'com.google.Chrome'


def meta(result):
    return next(json.loads(b.text) for b in result.content
                if b.type == 'text' and b.text.startswith('{') and '"view_id"' in b.text)


def state(result):
    for block in result.content:
        if block.type != 'text':
            continue
        for value in re.findall(r'AXStaticText name=("(?:[^"\\]|\\.)*")', block.text):
            name = json.loads(value)
            if name.startswith('MCP_EDITOR_STATE '):
                data = json.loads(name.removeprefix('MCP_EDITOR_STATE '))
                assert data['fixture'] == 'opengcua-editor-regression-v1'
                return data
    raise AssertionError('Fixture state was not observed')


def element(result, name):
    for block in result.content:
        if block.type == 'text':
            match = re.search(r'\[(\d+)\] AXTextArea name="' + re.escape(name) + '"', block.text)
            if match:
                return int(match[1])
    raise AssertionError(f'Fixture editor not observed: {name}')


async def check(app_path, basic_only=False):
    params = StdioServerParameters(command=str(Path(app_path)/'Contents/MacOS/OpenGCUA'), args=['--mcp'])
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as client:
            await client.initialize()
            window_id = None
            verified_fixture = False
            async def call(name, **args):
                result = await client.call_tool(name, args)
                assert not result.isError, result
                return result
            try:
                status = json.loads((await call('status')).content[0].text)
                assert status['permission_host'] == 'OpenGCUA'
                assert status['driver']['permissions']['accessibility']
                print('Integrated permission host: OpenGCUA', flush=True)
                await call('list_apps')
                windows = json.loads((await call('list_windows', app=APP)).content[0].text)['windows']
                matches = [w for w in windows if w['title'] == TITLE]
                assert len(matches) == 1, 'Open exactly one dedicated regression window'
                window_id = matches[0]['id']
                for _ in range(3):
                    view = await call('observe', app=APP, window_id=window_id)
                    try:
                        state(view)
                        break
                    except AssertionError:
                        await asyncio.sleep(0.3)
                state(view)
                verified_fixture = True
                cases = [('rich', '富文本替换 OpenGCUA ✅\n第二行 replacement', False)]
                if not basic_only:
                    cases += [
                        ('textarea', '多行替换 OpenGCUA 😀\n第二行 replacement', False),
                        ('rich', '\n开头换行\n\n保留空行 😀\n', True),
                        ('textarea', '\n开头换行\n\n保留空行 😀\n', True),
                        ('rich', 'CRLF 第一行\r\n第二行\r第三行', True),
                        ('rich', '\n', True),
                        ('rich', '单行替换 unchanged ✅', False),
                    ]
                for field, payload, targeted in cases:
                    label = 'MCP regression rich editor' if field == 'rich' else 'MCP regression textarea'
                    sibling = 'textarea' if field == 'rich' else 'rich'
                    before = state(view)
                    view = await call('click', view_id=meta(view)['view_id'], element=element(view,label))
                    view = await call('press_key', view_id=meta(view)['view_id'], key='a', modifiers=['cmd'], element=element(view,label))
                    selected = state(view)
                    if not selected[field+'All']:
                        # Chrome's native Select All omits a terminal LF from the
                        # DOM range. Do not normalize the output: replacement
                        # below must still match the complete payload exactly.
                        assert field == 'rich' and selected['richTextContent'].endswith('\n'), selected
                        assert selected['richSelected'] == selected['richTextContent'][:-1], selected
                        print('Chrome selection omits terminal LF; checking exact replacement',flush=True)
                    kwargs = {'element':element(view,label)} if targeted else {}
                    view = await call('type_text', view_id=meta(view)['view_id'], text_value=payload, **kwargs)
                    actual = state(view)
                    expected = payload.replace('\r\n','\n').replace('\r','\n')
                    assert actual[field] == expected, {'field':field,'expected':expected,'actual':actual[field],'html':actual['richHTML']}
                    assert actual[sibling] == before[sibling], 'Sibling editor was changed'
                    print(f'PASS {field} targeted={targeted}: {expected!r}',flush=True)
                print('All exact-value and selection checks passed',flush=True)
            finally:
                try:
                    if verified_fixture:
                        view = await call('observe', app=APP, window_id=window_id)
                        state(view)
                        await call('press_key', view_id=meta(view)['view_id'], key='w', modifiers=['cmd'])
                        print('Dedicated test window closed',flush=True)
                finally:
                    result = await call('release')
                    assert json.loads(result.content[0].text)['released']
                    print('MCP session released',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundled-app',default='/Applications/OpenGCUA.app')
    parser.add_argument('--basic-only',action='store_true')
    args=parser.parse_args()
    asyncio.run(check(args.bundled_app,args.basic_only))
