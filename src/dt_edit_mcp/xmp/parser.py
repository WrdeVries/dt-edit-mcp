"""Read and write Darktable XMP sidecar files."""
from __future__ import annotations

import copy
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

from .namespaces import DT, RDF, NS, dt, rdf

_NSMAP = {k: v for k, v in NS.items()}

BLANK_XMP = """\
<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:darktable="http://darktable.sf.net/"
      darktable:xmp_version="5"
      darktable:raw_params="0"
      darktable:auto_presets_applied="1"
      darktable:history_end="0"
      darktable:iop_order_version="2">
   <darktable:history><rdf:Seq/></darktable:history>
   <darktable:masks_history><rdf:Seq/></darktable:masks_history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""


@dataclass
class HistoryEntry:
    num: int
    operation: str
    enabled: bool
    modversion: int
    params: str          # hex or gz##<base64>
    blendop_version: int
    blendop_params: str  # hex or gz##<base64>
    multi_name: str = ""
    multi_priority: int = 0


@dataclass
class XmpDoc:
    path: Path
    xmp_version: int
    raw_params: int
    auto_presets_applied: int
    history_end: int
    iop_order_version: int
    iop_order_list: list[str]
    history: list[HistoryEntry]
    _tree: object = field(default=None, repr=False)
    _desc: object = field(default=None, repr=False)

    def clone(self) -> "XmpDoc":
        c = copy.deepcopy(self)
        return c


def _desc_of(tree) -> object:
    root = tree.getroot()
    rdf_el = root.find(f"{{{RDF}}}RDF")
    return rdf_el.find(f"{{{RDF}}}Description")


def _parse_iop_order(desc) -> list[str]:
    el = desc.find(f"{{{DT}}}iop_order_list")
    if el is None:
        return []
    seq = el.find(f"{{{RDF}}}Seq")
    if seq is None:
        return []
    return [li.get(f"{{{DT}}}operation", li.text or "") for li in seq]


def load(path: Path) -> XmpDoc:
    tree = etree.parse(str(path))
    desc = _desc_of(tree)

    def ga(name, default="0"):
        return desc.get(f"{{{DT}}}{name}", default)

    history: list[HistoryEntry] = []
    hist_el = desc.find(f"{{{DT}}}history")
    if hist_el is not None:
        seq = hist_el.find(f"{{{RDF}}}Seq")
        if seq is not None:
            for li in seq:
                history.append(HistoryEntry(
                    num=int(li.get(f"{{{DT}}}num", 0)),
                    operation=li.get(f"{{{DT}}}operation", ""),
                    enabled=li.get(f"{{{DT}}}enabled", "1") == "1",
                    modversion=int(li.get(f"{{{DT}}}modversion", 0)),
                    params=li.get(f"{{{DT}}}params", ""),
                    blendop_version=int(li.get(f"{{{DT}}}blendop_version", 0)),
                    blendop_params=li.get(f"{{{DT}}}blendop_params", ""),
                    multi_name=li.get(f"{{{DT}}}multi_name", ""),
                    multi_priority=int(li.get(f"{{{DT}}}multi_priority", 0)),
                ))

    return XmpDoc(
        path=path,
        xmp_version=int(ga("xmp_version", "5")),
        raw_params=int(ga("raw_params", "0")),
        auto_presets_applied=int(ga("auto_presets_applied", "1")),
        history_end=int(ga("history_end", "0")),
        iop_order_version=int(ga("iop_order_version", "2")),
        iop_order_list=_parse_iop_order(desc),
        history=history,
        _tree=tree,
        _desc=desc,
    )


def load_or_create(path: Path) -> XmpDoc:
    if path.exists():
        return load(path)
    tree = etree.fromstring(BLANK_XMP.encode()).getroottree()  # type: ignore[attr-defined]
    # lxml needs a proper parse
    tree = etree.ElementTree(etree.fromstring(BLANK_XMP.encode()))
    desc = _desc_of(tree)
    return XmpDoc(
        path=path,
        xmp_version=5,
        raw_params=0,
        auto_presets_applied=1,
        history_end=0,
        iop_order_version=2,
        iop_order_list=[],
        history=[],
        _tree=tree,
        _desc=desc,
    )


def _rebuild_tree(doc: XmpDoc) -> bytes:
    """Serialize XmpDoc back to XMP bytes."""
    tree = doc._tree
    desc = doc._desc

    def sa(name, value):
        desc.set(f"{{{DT}}}{name}", str(value))

    sa("xmp_version", doc.xmp_version)
    sa("raw_params", doc.raw_params)
    sa("auto_presets_applied", doc.auto_presets_applied)
    sa("history_end", doc.history_end)
    sa("iop_order_version", doc.iop_order_version)

    # Rebuild history Seq
    hist_el = desc.find(f"{{{DT}}}history")
    if hist_el is None:
        hist_el = etree.SubElement(desc, f"{{{DT}}}history")
    seq = hist_el.find(f"{{{RDF}}}Seq")
    if seq is not None:
        hist_el.remove(seq)
    seq = etree.SubElement(hist_el, f"{{{RDF}}}Seq")

    for e in doc.history:
        li = etree.SubElement(seq, f"{{{RDF}}}li")
        li.set(f"{{{DT}}}num", str(e.num))
        li.set(f"{{{DT}}}operation", e.operation)
        li.set(f"{{{DT}}}enabled", "1" if e.enabled else "0")
        li.set(f"{{{DT}}}modversion", str(e.modversion))
        li.set(f"{{{DT}}}params", e.params)
        li.set(f"{{{DT}}}multi_name", e.multi_name)
        li.set(f"{{{DT}}}multi_priority", str(e.multi_priority))
        li.set(f"{{{DT}}}blendop_version", str(e.blendop_version))
        li.set(f"{{{DT}}}blendop_params", e.blendop_params)

    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def save(doc: XmpDoc, path: Optional[Path] = None, max_backups: int = 10) -> None:
    """Atomic write with rolling backup. Safe on same-filesystem; retries on WinError 32."""
    target = path or doc.path
    data = _rebuild_tree(doc)

    tmp = target.with_suffix(".xmp.tmp")
    tmp.write_bytes(data)

    # Rolling backup
    if target.exists():
        _rotate_backup(target, max_backups)

    # Atomic rename with retry for OneDrive/AV locks
    _atomic_replace(tmp, target)


def _rotate_backup(target: Path, n: int) -> None:
    bak = target.with_suffix(f".xmp.bak.0")
    for i in range(n - 1, 0, -1):
        src = target.with_suffix(f".xmp.bak.{i - 1}")
        dst = target.with_suffix(f".xmp.bak.{i}")
        if src.exists():
            src.replace(dst)
    if target.exists():
        import shutil
        shutil.copy2(target, bak)


def _atomic_replace(src: Path, dst: Path, retries: int = 5) -> None:
    delay = 0.1
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
