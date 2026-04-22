"""
Apply two fixes to the clean xtools-mini codebase:
  FIX 1 - Cookie: always default to 'file' mode so uploaded cookies are used
  FIX 2 - Bulk Reels tab + backend API route
"""
import os
import sys
import re

# Force UTF-8 on stdout to avoid Windows cp1252 issues
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = r"d:\Desktop\13th August 2023\python-output\python-inputs\a-process-telegram-uploads\xtools-mini"
HTML = os.path.join(BASE, "templates", "youtube.html")
APP  = os.path.join(BASE, "app.py")

errors = []

# ============================================================
# PATCH 1: youtube.html
# ============================================================
with open(HTML, "r", encoding="utf-8") as f:
    html = f.read()

# --- NAV: add Bulk Reels button ---
OLD_NAV = '    <button class="btn btn-sm btn-ghost" id="ytab-cookies-btn" onclick="switchYTab(\'cookies\')">🍪 Cookies</button>\n  </div>'
NEW_NAV = '    <button class="btn btn-sm btn-ghost" id="ytab-bulkreel-btn" onclick="switchYTab(\'bulkreel\')">📑 Bulk Reels</button>\n    <button class="btn btn-sm btn-ghost" id="ytab-cookies-btn" onclick="switchYTab(\'cookies\')">🍪 Cookies</button>\n  </div>'
if OLD_NAV in html:
    html = html.replace(OLD_NAV, NEW_NAV)
    print("[OK] Nav button added")
else:
    errors.append("NAV button target not found")

# --- switchYTab: register bulkreel ---
OLD_SWITCH = "['dl','audio','reel','cookies'].forEach"
NEW_SWITCH = "['dl','audio','reel','bulkreel','cookies'].forEach"
if OLD_SWITCH in html:
    html = html.replace(OLD_SWITCH, NEW_SWITCH)
    print("[OK] switchYTab updated")
else:
    errors.append("switchYTab target not found")

# --- Bulk Reels TAB HTML ---
BULK_TAB_HTML = r"""
<!-- =====================================================
     TAB: BULK REELS
===================================================== -->
<div id="ytab-bulkreel" style="display:none;">
<div class="two-col">

  <!-- LEFT: Settings -->
  <div style="display:flex;flex-direction:column;gap:16px;">

    <div class="card">
      <div class="card-header">
        <div class="card-title">&#x25B6; Bulk YouTube URLs</div>
        <span id="bulk-reel-url-count" class="badge badge-gray">0 URLs</span>
      </div>
      <div class="card-body">
        <div class="form-group">
          <label class="form-label">YouTube Links <span style="color:var(--text-muted);font-weight:400;">(one per line)</span></label>
          <textarea class="form-textarea mono" id="bulk-reel-urls" rows="7"
            placeholder="https://www.youtube.com/watch?v=XXXX&#10;https://youtu.be/YYYY&#10;..."
            oninput="document.getElementById('bulk-reel-url-count').textContent = this.value.split('\n').filter(l=>l.trim().startsWith('http')).length + ' URLs'"></textarea>
          <div class="form-hint" style="margin-top:8px;">
            &#x26A1; <b>Auto-Thumbnail:</b> Each video's own YouTube thumbnail is automatically downloaded and placed as the bottom overlay - no manual selection needed.
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">&#x2702; Split Settings</div></div>
      <div class="card-body">
        <div class="form-group">
          <label class="form-label">Part Duration
            <span id="bulk-part-dur-val" style="font-family:var(--font-mono);color:var(--accent);margin-left:8px;">60s</span>
          </label>
          <input class="form-input" id="bulk-reel-part-dur" type="range" min="15" max="600" value="60"
            oninput="document.getElementById('bulk-part-dur-val').textContent=this.value+'s'">
        </div>
        <div class="form-group" style="margin-top:10px;">
          <label class="form-label">Quality</label>
          <select class="form-select" id="bulk-reel-quality">
            <option value="1080">1080p</option>
            <option value="720">720p</option>
            <option value="480">480p</option>
            <option value="best">Best Available</option>
          </select>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">&#x270F; Overlays</div></div>
      <div class="card-body" style="display:flex;flex-direction:column;gap:10px;">

        <label style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-card-2);cursor:pointer;">
          <input type="checkbox" id="bulk-overlay-title" checked style="accent-color:var(--accent);margin-top:3px;">
          <div style="flex:1;">
            <div style="font-size:13px;font-weight:600;">&#x1F4CC; Video Title (Top)</div>
            <div style="font-size:11px;color:var(--text-muted);">Auto-harvested from YouTube metadata</div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
              <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;">Position from top</span>
              <input type="range" id="bulk-title-pos-pct" min="5" max="50" value="20" style="flex:1;accent-color:var(--accent);"
                oninput="document.getElementById('bulk-title-pos-val').textContent=this.value+'%'">
              <span id="bulk-title-pos-val" style="font-size:11px;font-weight:600;min-width:32px;font-family:var(--font-mono)">20%</span>
            </div>
          </div>
        </label>

        <label style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-card-2);cursor:pointer;">
          <input type="checkbox" id="bulk-overlay-part" checked style="accent-color:var(--accent);margin-top:3px;">
          <div style="flex:1;">
            <div style="font-size:13px;font-weight:600;">&#x1F522; Part Number (Bottom Area)</div>
            <div style="font-size:11px;color:var(--text-muted);">"Part -1", "Part -2" &hellip;</div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
              <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;">Position from top</span>
              <input type="range" id="bulk-part-pos-pct" min="50" max="95" value="82" style="flex:1;accent-color:var(--accent);"
                oninput="document.getElementById('bulk-part-pos-val').textContent=this.value+'%'">
              <span id="bulk-part-pos-val" style="font-size:11px;font-weight:600;min-width:32px;font-family:var(--font-mono)">82%</span>
            </div>
          </div>
        </label>

        <label style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-card-2);cursor:pointer;">
          <input type="checkbox" id="bulk-overlay-watermark" style="accent-color:var(--accent);margin-top:3px;">
          <div style="flex:1;">
            <div style="font-size:13px;font-weight:600;">&#x1F4A7; Watermark</div>
            <input class="form-input" id="bulk-reel-watermark" type="text" placeholder="@YourChannel" style="margin-top:8px;font-size:12px;">
          </div>
        </label>

        <div style="padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-card-2);">
          <div style="font-size:13px;font-weight:600;margin-bottom:8px;">&#x1F5BC; Thumbnail Overlay Constraints</div>
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
            <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;min-width:50px;">Fill %</span>
            <input type="range" id="bulk-overlay-comp-pct" min="1" max="100" value="100" style="flex:1;accent-color:var(--accent);"
              oninput="document.getElementById('bulk-overlay-comp-val').textContent=this.value+'%'">
            <span id="bulk-overlay-comp-val" style="font-size:11px;font-weight:600;min-width:32px;font-family:var(--font-mono)">100%</span>
          </div>
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;min-width:50px;">Zoom %</span>
            <input type="range" id="bulk-overlay-zoom-pct" min="100" max="200" value="100" style="flex:1;accent-color:var(--accent);"
              oninput="document.getElementById('bulk-overlay-zoom-val').textContent=this.value+'%'">
            <span id="bulk-overlay-zoom-val" style="font-size:11px;font-weight:600;min-width:32px;font-family:var(--font-mono)">100%</span>
          </div>
        </div>

      </div>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">&#x1F4C2; Output</div></div>
      <div class="card-body">
        <div class="form-group">
          <label class="form-label">Output Folder <span style="color:var(--text-muted);font-weight:400;">(optional)</span></label>
          <div style="display:flex;gap:8px;">
            <input class="form-input mono" id="bulk-reel-out-dir" type="text" placeholder="Leave blank to use default YouTube dir" style="flex:1;">
            <button class="btn btn-secondary btn-sm" onclick="browseFolder('bulk-reel-out-dir')" style="white-space:nowrap;padding:8px 16px;">Browse&hellip;</button>
          </div>
        </div>
        <label style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-card-2);margin-top:10px;cursor:pointer;">
          <input type="checkbox" id="bulk-reel-delete-source" style="accent-color:var(--accent);">
          <span style="font-size:12px;">Delete source video after successful split</span>
        </label>
      </div>
    </div>

    <button class="btn btn-primary btn-full btn-lg" id="bulk-reel-btn" onclick="startBulkReelConvert()"
            style="background:linear-gradient(135deg,#ff3cac,#8250ff);font-size:15px;padding:14px;">
      &#x1F3AC; Start Bulk Reel Generation
    </button>
  </div>

  <!-- RIGHT: Progress -->
  <div style="display:flex;flex-direction:column;gap:16px;">
    <div class="card" id="bulk-reel-progress-card" style="display:none;">
      <div class="card-header">
        <div class="card-title">&#x23F3; Processing Queue</div>
        <span class="badge badge-orange" id="bulk-reel-status">Running</span>
      </div>
      <div class="card-body">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-secondary);margin-bottom:6px;">
          <span id="bulk-reel-current">Starting&hellip;</span>
          <span id="bulk-reel-pct">0%</span>
        </div>
        <div class="progress-wrap">
          <div class="progress-bar animated" id="bulk-reel-bar" style="width:0%;background:linear-gradient(90deg,#ff3cac,#8250ff);"></div>
        </div>
        <div class="sse-log" id="bulk-reel-log" style="margin-top:12px;max-height:500px;overflow-y:auto;"></div>
      </div>
    </div>
    <div class="card" id="bulk-reel-placeholder" style="display:flex;align-items:center;justify-content:center;min-height:200px;border:2px dashed var(--border);">
      <div style="text-align:center;color:var(--text-muted);">
        <div style="font-size:36px;margin-bottom:12px;">&#x1F4D1;</div>
        <div style="font-size:14px;">Paste URLs on the left and hit Start</div>
        <div style="font-size:12px;margin-top:6px;">Each video's thumbnail is auto-placed in the bottom overlay</div>
      </div>
    </div>
  </div>

</div>
</div><!-- /ytab-bulkreel -->

"""

ENDBLOCK = "{% endblock %}\n\n{% block scripts %}"
if ENDBLOCK in html:
    html = html.replace(ENDBLOCK, BULK_TAB_HTML + ENDBLOCK)
    print("[OK] Bulk Reels tab HTML inserted")
else:
    errors.append("endblock target not found in youtube.html")

# --- Add JS function ---
BULK_JS = """// -- Bulk Reel Converter --------------------------------------------------
async function startBulkReelConvert() {
  const rawUrls = document.getElementById('bulk-reel-urls').value.trim();
  if (!rawUrls) { showToast('Paste at least one YouTube URL', 'warn'); return; }
  const urls = rawUrls.split('\\n').map(l=>l.trim()).filter(l=>l.startsWith('http'));
  if (!urls.length) { showToast('No valid URLs found', 'warn'); return; }

  const partDur   = parseInt(document.getElementById('bulk-reel-part-dur').value) || 60;
  const quality   = document.getElementById('bulk-reel-quality').value || '1080';
  const showTitle = document.getElementById('bulk-overlay-title').checked;
  const showPart  = document.getElementById('bulk-overlay-part').checked;
  const showWm    = document.getElementById('bulk-overlay-watermark').checked;
  const wm        = document.getElementById('bulk-reel-watermark').value.trim();
  const outDir    = document.getElementById('bulk-reel-out-dir').value.trim();
  const titlePos  = parseInt(document.getElementById('bulk-title-pos-pct').value) || 20;
  const partPos   = parseInt(document.getElementById('bulk-part-pos-pct').value) || 82;
  const compPct   = parseInt(document.getElementById('bulk-overlay-comp-pct').value) || 100;
  const zoomPct   = parseInt(document.getElementById('bulk-overlay-zoom-pct').value) || 100;
  const deleteSrc = document.getElementById('bulk-reel-delete-source').checked;

  const payload = {
    urls, part_duration: partDur, quality,
    show_title: showTitle, show_part_label: showPart, show_watermark: showWm,
    watermark: wm,
    title_pos_pct: titlePos, part_pos_pct: partPos,
    overlay_image_zoom: zoomPct / 100.0, overlay_image_comp_pct: compPct,
    output_dir: outDir, delete_source: deleteSrc,
    use_rotation: _ytRotationActive
  };

  document.getElementById('bulk-reel-progress-card').style.display = 'block';
  document.getElementById('bulk-reel-placeholder').style.display   = 'none';
  document.getElementById('bulk-reel-log').textContent = '';
  document.getElementById('bulk-reel-bar').style.width = '0%';
  document.getElementById('bulk-reel-btn').disabled    = true;
  document.getElementById('bulk-reel-btn').textContent = 'Processing...';
  document.getElementById('bulk-reel-status').className   = 'badge badge-orange';
  document.getElementById('bulk-reel-status').textContent = 'Running';

  const resp = await postJSON('/api/youtube/bulk-reel-convert', payload);
  if (!resp.job_id) {
    showToast(resp.error || 'Failed to start bulk job', 'error');
    document.getElementById('bulk-reel-btn').disabled    = false;
    document.getElementById('bulk-reel-btn').textContent = 'Start Bulk Reel Generation';
    return;
  }
  startSSEProgress({
    url: '/api/youtube/progress/' + resp.job_id,
    logId: 'bulk-reel-log', barId: 'bulk-reel-bar', statusId: 'bulk-reel-status',
    onComplete: function(d) {
      document.getElementById('bulk-reel-btn').disabled    = false;
      document.getElementById('bulk-reel-btn').textContent = 'Start Bulk Reel Generation';
      document.getElementById('bulk-reel-status').className   = 'badge badge-green';
      document.getElementById('bulk-reel-status').textContent = 'Done';
      const ok = (d.results||[]).filter(r=>r.status==='ok').length;
      showToast(ok + ' reel parts created!', 'success');
    }
  });
}

"""
REEL_COMMENT = "// ── Reel Converter ────────────────────────────────────────────"
if REEL_COMMENT in html:
    html = html.replace(REEL_COMMENT, BULK_JS + REEL_COMMENT)
    print("[OK] Bulk Reel JS function added")
else:
    errors.append("JS reel comment target not found")

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)
print("[OK] youtube.html saved")


# ============================================================
# PATCH 2: app.py - Fix cookie default + add bulk endpoint
# ============================================================
with open(APP, "r", encoding="utf-8") as f:
    app_code = f.read()

# FIX: Cookie mode default from 'browser' to 'file'
OLD_CM = "    cookie_mode   = data.get('cookie_mode', 'browser')\n    request_delay = float(data.get('request_delay', 30.0))\n    use_rotation  = bool(data.get('use_rotation', False))\n\n    # \u2500\u2500 Multi-Account Rotation \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n    selected_account = None\n    cookie_file = None\n    if use_rotation:\n        selected_account = _get_available_yt_account()\n        if not selected_account:\n            return jsonify({'error': 'All YouTube accounts are exhausted or in cooldown. Please wait or add more accounts.'}), 429\n        cookie_file = selected_account['cookie_path']\n        cookie_mode = 'file'\n    else:\n        cookie_file = YT_COOKIES_FILE if cookie_mode == 'file' else None"
NEW_CM = "    cookie_mode   = data.get('cookie_mode', 'file')  # default: always use uploaded cookie\n    request_delay = float(data.get('request_delay', 30.0))\n    use_rotation  = bool(data.get('use_rotation', False))\n\n    # \u2500\u2500 Multi-Account Rotation \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n    selected_account = None\n    cookie_file = None\n    if use_rotation:\n        selected_account = _get_available_yt_account()\n        if not selected_account:\n            return jsonify({'error': 'All YouTube accounts are exhausted or in cooldown. Please wait or add more accounts.'}), 429\n        cookie_file = selected_account['cookie_path']\n        cookie_mode = 'file'\n    else:\n        # Always use uploaded cookie if it exists, regardless of frontend cookie_mode setting\n        cookie_file = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None"

if OLD_CM in app_code:
    app_code = app_code.replace(OLD_CM, NEW_CM)
    print("[OK] Cookie default fixed in reel-convert route")
else:
    print("[SKIP] Cookie default target not found - may already be patched or slightly different")

# ADD bulk endpoint
BULK_EP = '''
@app.route('/api/youtube/bulk-reel-convert', methods=['POST'])
def api_youtube_bulk_reel_convert():
    """
    Bulk: Download YouTube URLs sequentially, auto-inject thumbnail as bottom overlay,
    and split each video into 9:16 Instagram Reel parts.
    """
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}

    urls = [u.strip() for u in data.get('urls', []) if str(u).strip().startswith('http')]
    if not urls:
        return jsonify({'error': 'No valid YouTube URLs provided'}), 400

    output_dir    = data.get('output_dir', '').strip()
    watermark     = data.get('watermark', '').strip()
    part_secs     = int(data.get('part_duration', 60))
    quality       = data.get('quality', '1080')
    show_title    = bool(data.get('show_title', True))
    show_part     = bool(data.get('show_part_label', True))
    show_wm       = bool(data.get('show_watermark', bool(watermark)))
    title_pos_pct = float(data.get('title_pos_pct', 20.0))
    part_pos_pct  = float(data.get('part_pos_pct', 82.0))
    overlay_zoom  = float(data.get('overlay_image_zoom', 1.0) or 1.0)
    overlay_comp  = max(1.0, min(100.0, float(data.get('overlay_image_comp_pct', 100.0) or 100.0)))
    delete_source = bool(data.get('delete_source', False))
    use_rotation  = bool(data.get('use_rotation', False))
    # Always use uploaded cookie file if present
    global_cookie = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        from modules.reel_converter import convert_to_reels
        yt_dir = output_dir or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
        os.makedirs(yt_dir, exist_ok=True)
        total = len(urls)
        _emit(job, f'Bulk Reel Generation started -- {total} URL(s) queued', 2)

        all_results = []

        for idx, url in enumerate(urls, 1):
            base_pct = int((idx - 1) / total * 95)
            _emit(job, f'[{idx}/{total}] Processing: {url}', base_pct)

            # Strip playlist params -- process single video
            if 'v=' in url and 'list=' in url:
                m = re.search(r'(v=[^&]+)', url)
                if m: url = 'https://www.youtube.com/watch?' + m.group(1)

            selected_account = None
            c_file = global_cookie
            if use_rotation:
                selected_account = _get_available_yt_account()
                if not selected_account:
                    _emit(job, 'Skipping: No accounts available (all exhausted/cooldown)', None)
                    all_results.append({'url': url, 'status': 'error', 'error': 'No accounts available'})
                    continue
                c_file = selected_account['cookie_path']

            def dcb(line, pct=None):
                _emit(job, '  ' + line, None)

            # Download video + thumbnail automatically
            dl_results = download_youtube(
                urls=[url], quality=quality, output_dir=yt_dir,
                audio_only=False, custom_dir=bool(output_dir),
                browser=None, cookie_file=c_file,
                download_thumb=True,
                request_delay=30.0,
                progress_cb=dcb
            )

            if not dl_results or dl_results[0].get('status') not in ('ok', 'skipped'):
                err = dl_results[0].get('error', 'Download failed') if dl_results else 'Download failed'
                _emit(job, f'Download failed: {err}', None)
                if selected_account:
                    _update_yt_account_usage(selected_account['id'], success=False, error_msg=err)
                all_results.append({'url': url, 'status': 'error', 'error': err})
                continue

            if selected_account:
                _update_yt_account_usage(selected_account['id'], success=True)

            r = dl_results[0]
            video_path  = r.get('file_path', '')
            thumb_path  = r.get('thumbnail', '')
            video_title = r.get('title', 'Video')

            # Fallback: find newest mp4 in yt_dir
            if not video_path or not os.path.isfile(video_path):
                import glob as _g
                files = sorted(_g.glob(os.path.join(yt_dir, '**', '*.mp4'), recursive=True),
                               key=os.path.getmtime, reverse=True)
                video_path = files[0] if files else ''

            if not video_path or not os.path.isfile(video_path):
                _emit(job, 'Could not locate downloaded MP4', None)
                all_results.append({'url': url, 'status': 'error', 'error': 'MP4 not found after download'})
                continue

            _emit(job, f'Downloaded: {os.path.basename(video_path)} | Thumbnail: {os.path.basename(thumb_path) if thumb_path else "none"}', None)

            # Convert to reel parts, injecting auto-downloaded thumbnail
            reel_dir = output_dir or os.path.join(os.path.dirname(video_path), '_reels')
            os.makedirs(reel_dir, exist_ok=True)
            _emit(job, f'Splitting into {part_secs}s reel parts...', None)

            conv = convert_to_reels(
                input_path         = video_path,
                output_dir         = reel_dir,
                title              = video_title,
                watermark          = watermark,
                part_duration_sec  = part_secs,
                show_title         = show_title,
                show_part_label    = show_part,
                show_watermark     = show_wm,
                title_pos_pct      = title_pos_pct,
                part_pos_pct       = part_pos_pct,
                output_size        = 'instagram',
                overlay_image_path = thumb_path,
                overlay_image_zoom = overlay_zoom,
                overlay_image_comp_pct = overlay_comp,
                clip_start_sec     = 0,
                clip_end_sec       = 0,
                progress_cb        = lambda line: _emit(job, '  ' + line),
            )

            parts  = conv.get('parts', [])
            cvt_errors = conv.get('errors', [])
            _emit(job, f'{len(parts)} reel parts created for: {video_title}', None)

            for p in parts:
                all_results.append({'file': os.path.basename(p), 'path': p, 'status': 'ok'})
            for e in cvt_errors:
                all_results.append({'file': e, 'status': 'error'})

            if delete_source and parts:
                deleted, msg = _safe_remove_source(video_path, parts)
                _emit(job, msg)

        ok_count = len([r for r in all_results if r.get('status') == 'ok'])
        _emit(job, f'Bulk complete! {ok_count} reel parts from {total} video(s).', 100)
        _finish(job, f'Bulk done: {ok_count} parts', results=all_results)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


'''

LOCAL_ROUTE = "@app.route('/api/youtube/reel-convert-local', methods=['POST'])\ndef api_youtube_reel_convert_local():"
if LOCAL_ROUTE in app_code:
    if '/api/youtube/bulk-reel-convert' not in app_code:
        app_code = app_code.replace(LOCAL_ROUTE, BULK_EP + LOCAL_ROUTE)
        print("[OK] Bulk endpoint inserted into app.py")
    else:
        print("[SKIP] Bulk endpoint already present")
else:
    errors.append("reel-convert-local route not found in app.py")

with open(APP, "w", encoding="utf-8") as f:
    f.write(app_code)
print("[OK] app.py saved")

# ============================================================
# VERIFY syntax
# ============================================================
import subprocess
result = subprocess.run([sys.executable, '-m', 'py_compile', APP], capture_output=True, text=True)
if result.returncode == 0:
    print("[OK] app.py syntax verified - no errors")
else:
    errors.append("SYNTAX ERROR in app.py: " + result.stderr)

if errors:
    print("\n[WARNINGS]:")
    for e in errors:
        print("  -", e)
else:
    print("\nAll patches applied cleanly!")
