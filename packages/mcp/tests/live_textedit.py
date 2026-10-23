"""Opt-in installed MCP test in a fresh disposable TextEdit plain-text document."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def meta(result):
    return next(json.loads(b.text) for b in result.content
                if b.type=='text' and b.text.startswith('{') and '"view_id"' in b.text)


def editor(result):
    fields=[]
    for block in result.content:
        if block.type=='text':fields+=re.findall(r'\[(\d+)\] AXTextArea ',block.text)
    assert len(fields)==1, 'Expected exactly one document editor'
    return int(fields[0])


async def check(app):
    with tempfile.TemporaryDirectory(prefix='opengcua-native-',dir='/private/tmp') as root:
        document=Path(root)/'OpenGCUA-MCP-multiline.txt'
        document.write_text('Original native text 😀\n第二行\n',encoding='utf-8')
        proc=await asyncio.create_subprocess_exec('open','-a','TextEdit',str(document))
        assert await proc.wait()==0
        params=StdioServerParameters(command=str(Path(app)/'Contents/MacOS/OpenGCUA'),args=['--mcp'])
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as client:
                await client.initialize()
                async def call(name,**args):
                    result=await client.call_tool(name,args)
                    assert not result.isError,result
                    return result
                window=None
                try:
                    await call('list_apps')
                    windows=json.loads((await call('list_windows',app='com.apple.TextEdit')).content[0].text)['windows']
                    matches=[x for x in windows if x['title']==document.name]
                    assert len(matches)==1,matches
                    window=matches[0]['id']
                    view=await call('observe',app='com.apple.TextEdit',window_id=window)
                    view=await call('press_key',view_id=meta(view)['view_id'],key='a',modifiers=['cmd'],element=editor(view))
                    expected='\n原生文本替换 OpenGCUA 😀\n\n第二行 replacement\n'
                    view=await call('type_text',view_id=meta(view)['view_id'],text_value=expected,element=editor(view))
                    view=await call('press_key',view_id=meta(view)['view_id'],key='s',modifiers=['cmd'])
                    async with asyncio.timeout(3):
                        while document.read_text(encoding='utf-8')!=expected:await asyncio.sleep(0.05)
                    print('PASS TextEdit saved file: exact Unicode and leading/trailing/blank lines',flush=True)
                finally:
                    try:
                        if window:
                            view=await call('observe',app='com.apple.TextEdit',window_id=window)
                            await call('press_key',view_id=meta(view)['view_id'],key='w',modifiers=['cmd'])
                    finally:
                        result=await call('release');assert json.loads(result.content[0].text)['released']
                        print('TextEdit MCP session released',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundled-app',default='/Applications/OpenGCUA.app')
    args=parser.parse_args();asyncio.run(check(args.bundled_app))
