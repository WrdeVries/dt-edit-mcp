"""XMP parse/write roundtrip tests using a synthetic in-memory document."""
import tempfile
from pathlib import Path

import pytest

from dt_edit_mcp.xmp.parser import load_or_create, save, BLANK_XMP
from dt_edit_mcp.xmp.history import upsert_module
from dt_edit_mcp.codecs.blend import neutral as blend_neutral


def _temp_xmp() -> tuple[Path, Path]:
    d = Path(tempfile.mkdtemp())
    xmp = d / "test.xmp"
    xmp.write_text(BLANK_XMP, encoding="utf-8")
    return d, xmp


def test_blank_xmp_loads():
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    assert doc.history == []
    assert doc.history_end == 0
    assert doc.auto_presets_applied == 1


def test_upsert_and_reload():
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    bv, bp = blend_neutral()
    upsert_module(
        doc,
        operation="exposure",
        params="000000000000000040a0093b0000484200000000010000000100000000",
        modversion=7,
        enabled=True,
        blendop_version=bv,
        blendop_params=bp,
    )
    save(doc, xmp)

    doc2 = load_or_create(xmp)
    assert len(doc2.history) == 1
    assert doc2.history[0].operation == "exposure"
    assert doc2.history_end == 1


def test_history_end_correct():
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    bv, bp = blend_neutral()
    for op in ("exposure", "temperature"):
        upsert_module(doc, operation=op, params="00", modversion=1,
                      enabled=True, blendop_version=bv, blendop_params=bp)
    save(doc, xmp)

    doc2 = load_or_create(xmp)
    assert doc2.history_end == 2


def test_undo_redo_persists():
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    bv, bp = blend_neutral()
    upsert_module(doc, operation="exposure", params="00", modversion=1,
                  enabled=True, blendop_version=bv, blendop_params=bp)
    doc.history_end = 0
    save(doc, xmp)

    doc2 = load_or_create(xmp)
    assert doc2.history_end == 0
    assert len(doc2.history) == 1  # entry still exists, just inactive


def test_rolling_backup():
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    save(doc, xmp)
    bak = xmp.with_suffix(".xmp.bak.0")
    assert bak.exists()


def test_atomic_write_produces_valid_xml():
    from lxml import etree
    d, xmp = _temp_xmp()
    doc = load_or_create(xmp)
    save(doc, xmp)
    # Must parse cleanly
    etree.parse(str(xmp))
