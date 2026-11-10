#!/usr/bin/env python3
"""
Build a page that checks the WebGL radar shader against the Python PNG renderer.

    python scripts/check_radar_gl_parity.py out.html [noise|gate|row]

Serve the folder (python -m http.server) and open the page in Chrome or the
STORM map's Chromium; it prints how many pixels differ.  "gate" data tests the
range lookup, "row" the azimuth lookup, "noise" both.  Last result on a Mac:
coverage identical, azimuth exact, about 1.2% of pixels one gate over at gate edges.
"""
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.radar_scan import RadarScan
from ui.map import radar_overlay as ro
from ui.map import radar_gl

out = sys.argv[1]
grid = 512
table = np.full(256, np.nan, dtype=np.float32); table[2:] = (np.arange(2, 256) - 2) * 0.5 - 32.0
rng = np.random.default_rng(1)
n_az, n_g = 720, 1840
mode = sys.argv[2] if len(sys.argv) > 2 else "noise"
if mode == "gate":
    raw = np.broadcast_to((np.arange(n_g) % 200 + 50).astype(np.uint8), (n_az, n_g)).copy()
elif mode == "row":
    raw = np.broadcast_to((np.arange(n_az) % 200 + 50).astype(np.uint8)[:, None], (n_az, n_g)).copy()
else:
    raw = rng.integers(0, 256, size=(n_az, n_g), dtype=np.uint8)
# uneven start azimuth like real Level 3 radials
scan = RadarScan(site="KTLX", product="N0B", scan_time=datetime(2026,1,1,tzinfo=timezone.utc), raw=raw,
    vmin=-32.0, vmax=90.0, units="dBZ", colormap="nws_ref", radar_lat=35.333, radar_lon=-97.278,
    azimuths_deg=(0.27 + np.arange(n_az) * 0.5) % 360, first_gate_m=0.0, gate_width_m=250.0, value_table=table)
png, bounds, _ = ro.render_scan_to_png(scan, grid)
p = radar_gl.build_payload(scan)
x_min, y_min, x_max, y_max = ro._footprint_merc(scan)
C = 2 * np.pi * ro._MERC_R
dx = (x_max - x_min) / (grid - 1); dy = (y_max - y_min) / (grid - 1)
rx, ry = ro._lonlat_to_merc(np.array(scan.radar_lon), np.array(scan.radar_lat))
offs = dict(ox0=(x_min - dx/2 - rx)/ro._MERC_R, ox1=(x_max + dx/2 - rx)/ro._MERC_R,
            oyN=(y_max + dy/2 - ry)/ro._MERC_R, oyS=(y_min - dy/2 - ry)/ro._MERC_R)
lat_r = np.radians(scan.radar_lat); urad = [float(lat_r), float(np.tanh(float(ry)/ro._MERC_R/2)), 0.0]
quad = dict(**{k: float(v) for k, v in offs.items()}, x0=0.5 + (x_min - dx/2)/C, x1=0.5 + (x_max + dx/2)/C, y0=0.5 - (y_max + dy/2)/C, y1=0.5 - (y_min - dy/2)/C)
html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "map", "map_template.html")).read()
fs = re.search(r"var _RADAR_GL_FS = (\[.*?\]\.join\('\\n'\));", html, re.S).group(1)
b64 = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()
page = f"""<!doctype html><meta charset=utf-8><title>parity</title><body style="background:#fff">
<canvas id=c width={grid} height={grid}></canvas><pre id=o>running</pre><script>
var FS = {fs};
var meta = {json.dumps(p['meta'])}, URAD = {json.dumps(urad)}, Q = {json.dumps(quad)}, G = {grid};
var srcs = {{data: "{b64(p['data_png'])}", az: "{b64(p['az_png'])}", lut: "{b64(p['lut_png'])}", cpu: "{b64(png)}"}};
var imgs = {{}}, left = 4;
for (var k in srcs) (function(k){{ var i = new Image(); i.onload = function(){{ imgs[k]=i; if(--left==0) run(); }}; i.src = srcs[k]; }})(k);
function tex(gl, img, f) {{ var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT,1); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);
  gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
  gl.texImage2D(gl.TEXTURE_2D,0,f,f,gl.UNSIGNED_BYTE,img);
  [gl.TEXTURE_MIN_FILTER,gl.TEXTURE_MAG_FILTER].forEach(function(p){{gl.texParameteri(gl.TEXTURE_2D,p,gl.NEAREST)}});
  [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T].forEach(function(p){{gl.texParameteri(gl.TEXTURE_2D,p,gl.CLAMP_TO_EDGE)}}); return t; }}
function run() {{
  var gl = document.getElementById('c').getContext('webgl', {{premultipliedAlpha:true, preserveDrawingBuffer:true}});
  function sh(t, s) {{ var x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x);
    if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(x); return x; }}
  var VS = 'attribute vec2 a_clip; attribute vec2 a_off; varying vec2 v_off; void main(){{ v_off=a_off; gl_Position=vec4(a_clip,0.,1.); }}';
  var pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr); gl.useProgram(pr);
  // clip y=+1 is the top row (north, smaller mercator y)
  var v = new Float32Array([-1,1,Q.ox0,Q.oyN, 1,1,Q.ox1,Q.oyN, -1,-1,Q.ox0,Q.oyS, 1,-1,Q.ox1,Q.oyS]);
  var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, v, gl.STATIC_DRAW);
  var ac = gl.getAttribLocation(pr,'a_clip'), am = gl.getAttribLocation(pr,'a_off');
  gl.enableVertexAttribArray(ac); gl.vertexAttribPointer(ac,2,gl.FLOAT,false,16,0);
  gl.enableVertexAttribArray(am); gl.vertexAttribPointer(am,2,gl.FLOAT,false,16,8);
  var T = [tex(gl,imgs.data,gl.LUMINANCE), tex(gl,imgs.az,gl.RGBA), tex(gl,imgs.lut,gl.RGBA)];
  ['u_data','u_az','u_lut'].forEach(function(n,i){{ gl.activeTexture(gl.TEXTURE0+i); gl.bindTexture(gl.TEXTURE_2D,T[i]); gl.uniform1i(gl.getUniformLocation(pr,n),i); }});
  gl.uniform3fv(gl.getUniformLocation(pr,'u_radar'), URAD);
  gl.uniform4f(gl.getUniformLocation(pr,'u_gates'), meta.firstGateM, meta.gateWidthM, meta.farEdgeM, meta.nGates);
  gl.uniform3f(gl.getUniformLocation(pr,'u_dims'), meta.nAz, meta.azTexW, meta.azTexH);
  gl.uniform1f(gl.getUniformLocation(pr,'u_opacity'), 1.0);
  gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
  var px = new Uint8Array(G*G*4); gl.readPixels(0,0,G,G,gl.RGBA,gl.UNSIGNED_BYTE,px);
  var cv = document.createElement('canvas'); cv.width=cv.height=G; var cx = cv.getContext('2d'); cx.drawImage(imgs.cpu,0,0);
  var cpu = cx.getImageData(0,0,G,G).data;
  var diff = 0, drawn = 0, cpuDrawn = 0;
  for (var r = 0; r < G; r++) for (var c = 0; c < G; c++) {{
    var gi = ((G-1-r)*G + c)*4, ci = (r*G + c)*4;   // readPixels is bottom-up
    var ga = px[gi+3] > 0, ca = cpu[ci+3] > 0; if (ga) drawn++; if (ca) cpuDrawn++;
    // readPixels gives premultiplied colour; undo it, allowing for its rounding
    var a = px[gi+3], tol = 2 + 255 / Math.max(a, 1);
    var bad = ga !== ca || Math.abs(a - cpu[ci+3]) > 1;
    for (var ch = 0; ga && !bad && ch < 3; ch++) bad = Math.abs(px[gi+ch] * 255 / a - cpu[ci+ch]) > tol;
    if (bad) diff++;
  }}
  window.RESULT = {{pixels: G*G, gpuDrawn: drawn, cpuDrawn: cpuDrawn, mismatched: diff, mismatchPct: 100*diff/(G*G)}};
  document.getElementById('o').textContent = JSON.stringify(window.RESULT);
}}
</script>"""
open(out, "w").write(page)
print("ok", len(page)//1024, "KB")
