"""Generate a self-contained HTML comparison viewer."""
from __future__ import annotations

import base64
from pathlib import Path

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>dt-edit-mcp compare: {label_a} vs {label_b}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #111; color: #ccc; font-family: sans-serif; }}
h1 {{ padding: 12px 16px; font-size: 14px; background: #222; }}
.controls {{ padding: 8px 16px; background: #1a1a1a; display:flex; gap:16px; align-items:center; }}
button {{ background:#333; color:#eee; border:none; padding:6px 12px; cursor:pointer; border-radius:4px; }}
button.active {{ background:#555; }}
label {{ font-size:13px; }}
input[type=range] {{ width:200px; }}
.viewer {{ position:relative; overflow:hidden; cursor:col-resize; }}
.img-a, .img-b {{ position:absolute; top:0; left:0; width:100%; }}
.img-b {{ clip-path: inset(0 0 0 50%); }}
.divider {{ position:absolute; top:0; width:3px; background:rgba(255,255,255,0.6); cursor:col-resize; }}
.label {{ position:absolute; top:8px; background:rgba(0,0,0,0.55); padding:2px 8px; border-radius:3px; font-size:13px; }}
.label-a {{ left:8px; }}
.label-b {{ right:8px; }}
#side-by-side-view img {{ max-width:50%; display:inline-block; vertical-align:top; }}
.hidden {{ display:none !important; }}
</style>
</head>
<body>
<h1>Compare: <strong>{label_a}</strong> vs <strong>{label_b}</strong></h1>
<div class="controls">
  <button id="btn-split" class="active" onclick="setMode('split')">Split wipe</button>
  <button id="btn-sbs" onclick="setMode('sbs')">Side by side</button>
  <button id="btn-toggle" onclick="toggle()">Toggle</button>
  <label>Split position: <input type="range" id="slider" min="0" max="100" value="50" oninput="updateSplit(this.value)"></label>
</div>

<div id="split-view" class="viewer" style="height:calc(100vh - 80px);">
  <img class="img-a" src="{src_a}" id="imgA" style="position:relative;width:100%;display:block;">
  <img class="img-b" src="{src_b}" id="imgB">
  <div class="divider" id="divider" style="left:50%;height:100%;"></div>
  <span class="label label-a">{label_a}</span>
  <span class="label label-b">{label_b}</span>
</div>

<div id="sbs-view" class="hidden" style="height:calc(100vh - 80px);overflow:auto;">
  <div id="side-by-side-view">
    <img src="{src_a}" style="max-width:49.5%;display:inline-block;vertical-align:top;">
    <img src="{src_b}" style="max-width:49.5%;display:inline-block;vertical-align:top;">
  </div>
  <div style="padding:8px 16px;display:flex;justify-content:space-around;">
    <span>{label_a}</span><span>{label_b}</span>
  </div>
</div>

<script>
var mode='split', showA=true;
function setMode(m) {{
  mode=m;
  document.getElementById('split-view').classList.toggle('hidden', m!=='split');
  document.getElementById('sbs-view').classList.toggle('hidden', m!=='sbs');
  ['btn-split','btn-sbs','btn-toggle'].forEach(id=>document.getElementById(id).classList.remove('active'));
  if(m==='split') document.getElementById('btn-split').classList.add('active');
  if(m==='sbs') document.getElementById('btn-sbs').classList.add('active');
}}
function updateSplit(v) {{
  var pct=v+'%';
  document.getElementById('imgB').style.clipPath='inset(0 0 0 '+pct+')';
  document.getElementById('divider').style.left=pct;
}}
function toggle() {{
  showA=!showA;
  document.getElementById('imgA').style.opacity=showA?1:0;
  document.getElementById('imgB').style.clipPath=showA?'inset(0 0 0 50%)':'inset(0)';
  document.getElementById('btn-toggle').classList.toggle('active');
}}
// Drag support
var dragging=false;
document.getElementById('split-view').addEventListener('mousedown',function(e){{dragging=true;}});
document.addEventListener('mouseup',function(){{dragging=false;}});
document.addEventListener('mousemove',function(e){{
  if(!dragging||mode!=='split') return;
  var rect=document.getElementById('split-view').getBoundingClientRect();
  var pct=Math.max(0,Math.min(100,(e.clientX-rect.left)/rect.width*100));
  document.getElementById('slider').value=pct;
  updateSplit(pct);
}});
</script>
</body>
</html>
"""


def write(
    img_a: Path,
    img_b: Path,
    label_a: str,
    label_b: str,
    output: Path,
    embed_images: bool = True,
) -> Path:
    """Write a self-contained HTML comparison file."""
    if embed_images:
        src_a = _embed(img_a)
        src_b = _embed(img_b)
    else:
        src_a = img_a.as_uri()
        src_b = img_b.as_uri()

    html = _TEMPLATE.format(
        label_a=label_a,
        label_b=label_b,
        src_a=src_a,
        src_b=src_b,
    )
    output.write_text(html, encoding="utf-8")
    return output


def _embed(img: Path) -> str:
    data = img.read_bytes()
    b64 = base64.b64encode(data).decode()
    ext = img.suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{b64}"
