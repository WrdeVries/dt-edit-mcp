"""History stack operations on XmpDoc."""
from __future__ import annotations

from .parser import XmpDoc, HistoryEntry
from ..codecs.iop_order import default_iop_order, insert_position


def find_entry(doc: XmpDoc, operation: str, instance: int = 0) -> int | None:
    """Return index into doc.history (not num) for the given op+instance, or None."""
    count = 0
    for i, e in enumerate(doc.history):
        if e.operation == operation:
            if count == instance:
                return i
            count += 1
    return None


def upsert_module(
    doc: XmpDoc,
    operation: str,
    params: str,
    modversion: int,
    enabled: bool,
    blendop_version: int,
    blendop_params: str,
    instance: int = 0,
    multi_name: str = "",
) -> HistoryEntry:
    """
    Insert or replace a module history entry.

    If history_end < len(history), truncate forward entries first
    (text-editor undo semantics: branching is not supported).
    """
    # Truncate forward history if cursor is behind the end
    if doc.history_end < len(doc.history):
        doc.history = doc.history[:doc.history_end]

    idx = find_entry(doc, operation, instance)

    entry = HistoryEntry(
        num=0,  # will be renumbered below
        operation=operation,
        enabled=enabled,
        modversion=modversion,
        params=params,
        blendop_version=blendop_version,
        blendop_params=blendop_params,
        multi_name=multi_name,
        multi_priority=instance,
    )

    if idx is not None:
        doc.history[idx] = entry
    else:
        # Place at the correct IOP-order position among existing entries
        pos = _iop_insert_pos(doc, operation)
        doc.history.insert(pos, entry)

    # Renumber all entries sequentially
    for i, e in enumerate(doc.history):
        e.num = i

    doc.history_end = len(doc.history)
    return entry


def _iop_insert_pos(doc: XmpDoc, operation: str) -> int:
    """Find the right index to insert `operation` respecting IOP order."""
    order = doc.iop_order_list or default_iop_order()
    try:
        target_rank = order.index(operation)
    except ValueError:
        return len(doc.history)  # unknown module goes at end

    for i, e in enumerate(doc.history):
        try:
            rank = order.index(e.operation)
        except ValueError:
            rank = len(order)
        if rank > target_rank:
            return i
    return len(doc.history)


def set_history_end(doc: XmpDoc, end: int) -> None:
    doc.history_end = max(0, min(end, len(doc.history)))


def disable_module(doc: XmpDoc, operation: str, instance: int = 0) -> bool:
    idx = find_entry(doc, operation, instance)
    if idx is None:
        return False
    doc.history[idx].enabled = False
    return True
