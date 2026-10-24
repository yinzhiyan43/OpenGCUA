"""Literal multiline input through the SDK's guarded foreground route."""
from .clipboard import temporary_literal_text


async def type_literal_text(target, value, element=None):
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    await target.activate()
    if '\n' not in value:
        return await target.ui.input.text(value, target=element, delivery='foreground')
    # Empty targeted input establishes focus without clicking into/replacing the
    # selection. The subsequent chord must NOT target the node: the driver's web
    # chord targeting uses a focus click, which collapses the selected range.
    if element is not None:
        await target.ui.input.text('', target=element, delivery='foreground')
    with temporary_literal_text(value):
        return await target.ui.input.press_key('v', modifiers=['cmd'], delivery='foreground')
