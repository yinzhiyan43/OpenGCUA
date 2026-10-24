from types import SimpleNamespace
import unittest
from unittest.mock import patch
from opengcua_mcp.clipboard import temporary_literal_text


class Item:
    def __init__(self,data=None):self.data=dict(data or {})
    @classmethod
    def alloc(cls):return cls()
    def init(self):return self
    def types(self):return list(self.data)
    def dataForType_(self,kind):return self.data[kind]
    def setData_forType_(self,value,kind):self.data[kind]=value;return True
    def setString_forType_(self,value,kind):return self.setData_forType_(value,kind)


class Board:
    def __init__(self,items):self.items=items;self.count=0;self.writes=0;self.fail_stage=False
    def changeCount(self):return self.count
    def pasteboardItems(self):return self.items
    def clearContents(self):self.count+=1;self.items=[];return self.count
    def writeObjects_(self,items):
        self.writes+=1
        if self.fail_stage and self.writes==1:return False
        self.items=items;self.count+=1;return True


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.original=[{'public.utf8-plain-text':b'old text','public.rtf':b'{rtf}'},{'public.png':b'\x89PNG'}]
        self.board=Board([Item(x) for x in self.original])
        module=SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda:self.board),
                               NSPasteboardItem=Item,NSPasteboardTypeString='public.utf8-plain-text',NSPasteboardTypeHTML='public.html')
        self.patch=patch.dict('sys.modules',{'AppKit':module});self.patch.start();self.addCleanup(self.patch.stop)

    def values(self):return [item.data for item in self.board.items]

    def test_restores_all_formats_and_items_after_plain_text_paste(self):
        with temporary_literal_text('a\nb'):
            self.assertEqual(self.values()[0]['public.utf8-plain-text'],'a\nb')
            self.assertIn('white-space: pre-wrap;',self.values()[0]['public.html'])
        self.assertEqual(self.values(),self.original)

    def test_failure_also_restores_clipboard(self):
        with self.assertRaisesRegex(RuntimeError,'paste refused'):
            with temporary_literal_text('a\nb'):raise RuntimeError('paste refused')
        self.assertEqual(self.values(),self.original)

    def test_newer_user_copy_is_never_overwritten(self):
        with temporary_literal_text('a\nb'):
            self.board.clearContents();self.board.writeObjects_([Item({'user':'new copy'})])
        self.assertEqual(self.values(),[{'user':'new copy'}])

    def test_unreadable_format_refuses_before_modifying_clipboard(self):
        self.board.items[0].data['unavailable']=None
        with self.assertRaisesRegex(RuntimeError,'CLIPBOARD_UNAVAILABLE'):
            with temporary_literal_text('a\nb'):self.fail('must not paste')
        self.assertEqual(self.board.count,0)
        self.assertEqual(self.board.writes,0)

    def test_changed_clipboard_during_snapshot_refuses_without_writes(self):
        original=self.board.items[0].dataForType_
        def changed(kind):self.board.count+=1;return original(kind)
        self.board.items[0].dataForType_=changed
        with self.assertRaisesRegex(RuntimeError,'CLIPBOARD_CHANGED'):
            with temporary_literal_text('a\nb'):self.fail('must not paste')
        self.assertEqual(self.board.writes,0)

    def test_failed_staging_restores_original(self):
        self.board.fail_stage=True
        with self.assertRaisesRegex(RuntimeError,'CLIPBOARD_UNAVAILABLE'):
            with temporary_literal_text('a\nb'):self.fail('must not paste')
        self.assertEqual(self.values(),self.original)

    def test_empty_clipboard_is_restored_to_empty(self):
        self.board.items=[]
        with temporary_literal_text('a\nb'):pass
        self.assertEqual(self.values(),[])

    def test_html_flavor_escapes_input_as_inert_text(self):
        value='<script>alert("x")</script>\n& <img src="https://invalid.example">'
        with temporary_literal_text(value):
            payload=self.values()[0]
            self.assertEqual(payload['public.utf8-plain-text'],value)
            self.assertNotIn('<script>',payload['public.html'])
            self.assertNotIn('<img',payload['public.html'])
            self.assertIn('&lt;script&gt;',payload['public.html'])
            self.assertIn('\n&amp;',payload['public.html'])

    def test_restore_failure_is_explicit_and_never_claims_success(self):
        original=self.board.writeObjects_
        def fail_restore(items):
            if self.board.writes == 1:return False
            return original(items)
        self.board.writeObjects_=fail_restore
        with self.assertRaisesRegex(RuntimeError,'CLIPBOARD_RESTORE_FAILED'):
            with temporary_literal_text('a\nb'):pass
