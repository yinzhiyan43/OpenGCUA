"""Temporary literal-text paste, preserving existing clipboard formats in memory."""
from contextlib import contextmanager
from html import escape


@contextmanager
def temporary_literal_text(value):
    # Lazy import: native AppKit is already part of the bundled macOS runtime.
    from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSPasteboardTypeHTML
    board = NSPasteboard.generalPasteboard()
    initial_count = board.changeCount()
    saved = []
    for original in board.pasteboardItems() or []:
        clone = NSPasteboardItem.alloc().init()
        for kind in original.types():
            data = original.dataForType_(kind)
            if data is None or not clone.setData_forType_(data, kind):
                raise RuntimeError('CLIPBOARD_UNAVAILABLE: could not preserve all clipboard formats; no input sent')
        saved.append(clone)
    payload = NSPasteboardItem.alloc().init()
    if not payload.setString_forType_(value, NSPasteboardTypeString):
        raise RuntimeError('CLIPBOARD_UNAVAILABLE: could not prepare plain text; no input sent')
    # Plain controls use the text flavor; rich editors use this inert HTML
    # representation to keep literal LF characters instead of paragraph breaks.
    fragment = '<meta charset="utf-8"><span style="white-space: pre-wrap;">' + escape(value) + '</span>'
    if not payload.setString_forType_(fragment, NSPasteboardTypeHTML):
        raise RuntimeError('CLIPBOARD_UNAVAILABLE: could not prepare literal line breaks; no input sent')
    if board.changeCount() != initial_count:
        raise RuntimeError('CLIPBOARD_CHANGED: clipboard changed while preparing input; no input sent')
    owned_count = board.clearContents()
    try:
        ok = board.writeObjects_([payload])
        owned_count = board.changeCount()
        if not ok:
            raise RuntimeError('CLIPBOARD_UNAVAILABLE: could not stage plain text; no input sent')
        yield
    finally:
        # Never overwrite a newer copy made by the user or another application.
        if board.changeCount() == owned_count:
            board.clearContents()
            if saved and not board.writeObjects_(saved):
                raise RuntimeError('CLIPBOARD_RESTORE_FAILED: input may have happened; do not repeat it blindly')
