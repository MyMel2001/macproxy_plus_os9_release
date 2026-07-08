"""
NewNet Extension for Macproxy (Browservice-like Streaming Plugin)
Fully handles browser interaction and text entry via image maps,
uses playwright-stealth to prevent bot detection, humanizes keystroke sync,
and streams actual modern webpage audio as an MP3 using pure-browser loopbacks.
"""

import os
import io
import time
import queue
import asyncio
import threading
import random
from flask import request, Response
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright
# Ensure you run: pip install playwright-stealth
from playwright_stealth import Stealth

DOMAIN = "newnet.lol"
VIEWPORT_WIDTH = 800
VIEWPORT_HEIGHT = 600

STREAMING_ENABLED = False
CURRENT_URL = "https://searx.nodemixaholic.com"

_LATEST_FRAME_BYTES = b""
_LATEST_MAP_LINKS = []

_PERSISTENT_PAGE = None
_BACKGROUND_LOOP = None

# Pure thread-safe thread queue to hold MP3 bytes generated straight from inside Chromium
AUDIO_QUEUE = queue.Queue(maxsize=5000)

# --- BACKGROUND LOOP & TICKER SECTION ---

def _start_background_loop():
    global _BACKGROUND_LOOP
    _BACKGROUND_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_BACKGROUND_LOOP)
    
    # Schedule our live continuous screen grabber task
    _BACKGROUND_LOOP.create_task(_live_screenshot_ticker())
    
    _BACKGROUND_LOOP.run_forever()

def get_background_loop():
    global _BACKGROUND_LOOP
    if _BACKGROUND_LOOP is None:
        t = threading.Thread(target=_start_background_loop, daemon=True)
        t.start()
        while _BACKGROUND_LOOP is None:
            time.sleep(0.01)
    return _BACKGROUND_LOOP

async def _live_screenshot_ticker():
    """Continuously captures the browser viewport at a smooth frame rate."""
    global _LATEST_FRAME_BYTES
    while True:
        try:
            if _PERSISTENT_PAGE:
                _LATEST_FRAME_BYTES = await _PERSISTENT_PAGE.screenshot(type="jpeg", quality=60)
        except Exception:
            pass
        await asyncio.sleep(0.066) # ~15 FPS

async def _init_persistent_browser():
	global _PERSISTENT_PAGE
	if _PERSISTENT_PAGE is None:
		p = await async_playwright().start()
		
		# 1. EVASION: Add standard automation evasion flags to Chromium initialization
		browser = await p.chromium.launch(headless=True, args=[
			"--autoplay-policy=no-user-gesture-required",
			"--use-fake-ui-for-media-stream",
			"--disable-blink-features=AutomationControlled",
			"--disable-features=IsolateOrigins,site-per-process"
		])
		
		# 2. EVASION: Spoof a real modern desktop user agent, locale, and viewport
		context = await browser.new_context(
			user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
			viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
			locale="en-US",
			timezone_id="America/New_York"
		)
		
        # 2. NEW EVASION METHOD: Use the v2.0+ Stealth engine configuration manager
		stealth = Stealth()
		await stealth.apply_stealth_async(context)  # Injects anti-bot protections context-wide

		# Open the page inheriting those context-wide rules
		_PERSISTENT_PAGE = await context.new_page()

		# Bridge to receive MP3 binary data streams pushed directly from the browser window JS engine
		await _PERSISTENT_PAGE.expose_binding("pushAudioChunk", lambda source, data: AUDIO_QUEUE.put(bytes(data)))
		
		await _PERSISTENT_PAGE.goto(CURRENT_URL, wait_until="load")
		await _inject_browser_audio_grabber()


async def _inject_browser_audio_grabber():
	"""
	Injects an active browser hook script that catches audio playing on the page,
	compresses it on the fly using standard Web Audio nodes, and hands it off to Python.
	"""
	if not _PERSISTENT_PAGE:
		return
	
	await _PERSISTENT_PAGE.evaluate("""() => {
		if (window.AudioStreamHooked) return;
		window.AudioStreamHooked = true;

		const script = document.createElement('script');
		script.src = 'https://cdnjs.cloudflare.com/ajax/libs/lamejs/1.2.1/lame.all.min.js';
		script.onload = () => {
			try {
				const AudioContext = window.AudioContext || window.webkitAudioContext;
				const audioCtx = new AudioContext();
				
				const mp3encoder = new lamejs.Mp3Encoder(2, audioCtx.sampleRate, 64);
				const processor = audioCtx.createScriptProcessor(4096, 2, 2);
				
				processor.onaudioprocess = (e) => {
					const left = e.inputBuffer.getChannelData(0);
					const right = e.inputBuffer.getChannelData(1);
					
					const leftInt = new Int16Array(left.length);
					const rightInt = new Int16Array(right.length);
					for (let i = 0; i < left.length; i++) {
						leftInt[i] = left[i] < 0 ? left[i] * 0x8000 : left[i] * 0x7FFF;
						rightInt[i] = right[i] < 0 ? right[i] * 0x8000 : right[i] * 0x7FFF;
					}
					
					const mp3buf = mp3encoder.encodeBuffer(leftInt, rightInt);
					if (mp3buf.length > 0) {
						window.pushAudioChunk(Array.from(new Uint8Array(mp3buf)));
					}
				};
				
				processor.connect(audioCtx.destination);
			} catch(err) { console.error("Audio capture injection issue:", err); }
		};
		document.head.appendChild(script);
	}""")

async def _update_state_and_map():
	global _LATEST_FRAME_BYTES, _LATEST_MAP_LINKS, CURRENT_URL
	if _PERSISTENT_PAGE:
		CURRENT_URL = _PERSISTENT_PAGE.url
		_LATEST_FRAME_BYTES = await _PERSISTENT_PAGE.screenshot(type="jpeg", quality=60)
		# Re-verify our background script is hooked after navigating
		await _inject_browser_audio_grabber()
		
		_LATEST_MAP_LINKS = await _PERSISTENT_PAGE.evaluate("""() => {
			const elements = [];
			document.querySelectorAll('a, button, input[type="submit"], input[type="button"], [role="button"]').forEach(el => {
				const rect = el.getBoundingClientRect();
				if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.left >= 0) {
					let destUrl = el.href || "";
					if (!destUrl && el.tagName !== 'A') { destUrl = "#click_action"; }
					elements.push({
						type: "click",
						x1: Math.round(rect.left), y1: Math.round(rect.top),
						x2: Math.round(rect.right), y2: Math.round(rect.bottom),
						url: destUrl,
						cx: Math.round(rect.left + (rect.width / 2)), cy: Math.round(rect.top + (rect.height / 2))
					});
				}
			});
			document.querySelectorAll('input[type="text"], input[type="search"], input[type="password"], textarea, [contenteditable="true"]').forEach(el => {
				const rect = el.getBoundingClientRect();
				if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.left >= 0) {
					elements.push({
						type: "text_entry",
						x1: Math.round(rect.left), y1: Math.round(rect.top),
						x2: Math.round(rect.right), y2: Math.round(rect.bottom),
						cx: Math.round(rect.left + (rect.width / 2)), cy: Math.round(rect.top + (rect.height / 2))
					});
				}
			});
			return elements;
		}""")

async def _navigate_persistent_async(url):
	await _init_persistent_browser()
	try:
		await _PERSISTENT_PAGE.goto(url, wait_until="load", timeout=30000)
		await _update_state_and_map()
	except Exception as e: print(f"Nav error: {e}")

async def _interact_persistent_async(x, y, keystrokes=None):
	await _init_persistent_browser()
	try:
		await _PERSISTENT_PAGE.mouse.click(x, y)
		if keystrokes:
			# 4. EVASION: Humanize keyboard synchronization with randomized millisecond typing lag
			for char in keystrokes:
				await _PERSISTENT_PAGE.keyboard.type(char)
				await asyncio.sleep(random.uniform(0.04, 0.12))
            # We don't want to auto-press enter in some cases (such as site login)
			# await _PERSISTENT_PAGE.keyboard.press("Enter")
		await _PERSISTENT_PAGE.wait_for_timeout(400)
		await _update_state_and_map()
	except Exception as e: print(f"Interact error: {e}")

def sync_navigate(url):
	future = asyncio.run_coroutine_threadsafe(_navigate_persistent_async(url), get_background_loop())
	future.result()

def sync_interact(x, y, keystrokes=None):
	future = asyncio.run_coroutine_threadsafe(_interact_persistent_async(x, y, keystrokes), get_background_loop())
	future.result()

def sync_get_latest():
	async def _get():
		await _init_persistent_browser()
		await _update_state_and_map()
	future = asyncio.run_coroutine_threadsafe(_get(), get_background_loop())
	future.result()

def build_streaming_viewport(links, input_focus_coords=None):
	global CURRENT_URL
	cb = str(int(time.time()))
	html = f'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<head>\n<title>Streaming Engine: {CURRENT_URL}</title>\n</head>\n<body bgcolor="#808080">\n'
	html += '<table border="1" cellpadding="4" cellspacing="0" bgcolor="#C0C0C0" width="100%">\n<tr><td>\n'
	html += '  <form action="/newnet-navigate" method="POST" style="margin:0;">\n'
	html += f'  <b>Remote Address:</b> <input type="text" name="nav_url" value="{CURRENT_URL}" size="50"> <input type="submit" value="Go">\n'
	html += '  </form>\n</td></tr>\n'
	html += '<td align="center" nowrap>\n'
	html += f'  <b>Scroll:</b> \n'
	html += f'  [<a href="/newnet-scroll?dir=up&cb={cb}">Up</a>]\n'
	html += f'  [<a href="/newnet-scroll?dir=down&cb={cb}">Down</a>]\n'
	html += f'  [<a href="/newnet-scroll?dir=left&cb={cb}">Left</a>]\n'
	html += f'  [<a href="/newnet-scroll?dir=right&cb={cb}">Right</a>]\n'
	html += '</td>\n'
	html += '</tr>\n'
	if input_focus_coords:
		html += f'<tr bgcolor="#FFFFD0"><td><form action="/newnet-send-keystrokes" method="POST" style="margin:0;"><input type="hidden" name="x" value="{input_focus_coords[0]}"><input type="hidden" name="y" value="{input_focus_coords[1]}"><b>Text Input:</b> <input type="text" name="typed_text" size="40"> <input type="submit" value="Type"></form></td></tr>\n'
	html += '</table><br>\n'
	html += f'<embed src="/newnet-audio.mp3?cb={cb}" width="2" height="2" autoplay="true" hidden="true" controller="false"></embed>\n\n'
	html += f'<map name="viewport_map_{cb}">\n'
	for link in links:
		if link.get("type") == "text_entry":
			html += f'  <area shape="rect" coords="{link["x1"]},{link["y1"]},{link["x2"]},{link["y2"]}" href="/newnet-render?focus_x={link["cx"]}&focus_y={link["cy"]}&cb={cb}">\n'
		else:
			html += f'  <area shape="rect" coords="{link["x1"]},{link["y1"]},{link["x2"]},{link["y2"]}" href="/newnet-viewport-click?x={link["cx"]}&y={link["cy"]}&cb={cb}">\n'
	html += f'</map>\n<center><form action="/newnet-viewport-click" method="GET"><input type="image" src="/newnet-frame.jpg?cb={cb}" usemap="#viewport_map_{cb}" border="0" ismap></form></center>\n</body>\n</html>'
	return html

def handle_request(req):
	global STREAMING_ENABLED, CURRENT_URL, _LATEST_FRAME_BYTES, _LATEST_MAP_LINKS
	parsed_url = urlparse(req.url)
	path = parsed_url.path
	query_params = parse_qs(parsed_url.query)
	is_gateway = (req.host == "newnet.lol" or "newnet.lol" in path)

	if STREAMING_ENABLED and not is_gateway:
		if req.url != CURRENT_URL and path not in ["/newnet-frame.jpg", "/newnet-audio.mp3", "/newnet-navigate", "/newnet-viewport-click", "/newnet-send-keystrokes"]:
			CURRENT_URL = req.url
			sync_navigate(CURRENT_URL)
		if not _LATEST_FRAME_BYTES: sync_get_latest()
		return build_streaming_viewport(_LATEST_MAP_LINKS), 200

	if path == "/newnet-render":
		focus_coords = None
		if "focus_x" in query_params and "focus_y" in query_params:
			focus_coords = (int(query_params["focus_x"][0]), int(query_params["focus_y"][0]))
		return build_streaming_viewport(_LATEST_MAP_LINKS, focus_coords), 200

	if path == "/newnet-frame.jpg":
		def frame_stream_generator():
			global _LATEST_FRAME_BYTES
			last_sent_frame = b""
			
			try:
				while True:
					# 1. Only yield if a brand new frame has been generated in the background
					if _LATEST_FRAME_BYTES and _LATEST_FRAME_BYTES != last_sent_frame:
						last_sent_frame = _LATEST_FRAME_BYTES
						
						yield (b'--frame\r\n'
						       b'Content-Type: image/jpeg\r\n'
						       b'Content-Length: ' + str(len(last_sent_frame)).encode() + b'\r\n\r\n' + 
						       last_sent_frame + b'\r\n')
					
					# 2. Yield control to match rendering cycle and avoid CPU exhaustion
					time.sleep(0.05)
			except (GeneratorExit, Exception):
				# CRITICAL FIX: This catches when the browser drops the connection 
				# (e.g., during a click or page navigation) and kills this thread.
				return

		resp = Response(frame_stream_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")
		resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
		resp.headers["Pragma"] = "no-cache"
		resp.headers["Expires"] = "0"
		return resp

	if path == "/newnet-audio.mp3":
		def pure_audio_generator():
			yield b'\x00' * 512
			while True:
				try:
					chunk = AUDIO_QUEUE.get(timeout=1.0)
					yield chunk
				except queue.Empty:
					yield b'\x00' * 64
		return Response(pure_audio_generator(), mimetype="audio/mpeg")

	if path == "/newnet-navigate" and req.method == "POST":
		new_dest = request.form.get("nav_url")
		if new_dest:
			if not new_dest.startswith("http"): new_dest = "https://" + new_dest
			CURRENT_URL = new_dest
			sync_navigate(CURRENT_URL)
		return '<html><head><meta http-equiv="refresh" content="0;url=/newnet-render"></head><body>Loading...</body></html>', 200

	if path == "/newnet-viewport-click":
		x, y = None, None
		if "x" in query_params and "y" in query_params:
			x, y = int(query_params["x"][0]), int(query_params["y"][0])
		else:
			try:
				coords = parsed_url.query.split(",")
				if len(coords) == 2: x, y = int(coords[0]), int(coords[1])
			except: pass
		if x is not None and y is not None: sync_interact(x, y)
		return '<html><head><meta http-equiv="refresh" content="0;url=/newnet-render"></head><body>Clicking...</body></html>', 200

    # --- NEW SCROLLING ENDPOINT ROUTE ---
	if path == "/newnet-scroll":
		direction = query_params.get("dir", ["down"])[0]
		
		scroll_x, scroll_y = 0, 0
		if direction == "up": scroll_y = -400
		elif direction == "down": scroll_y = 400
		elif direction == "left": scroll_x = -200
		elif direction == "right": scroll_x = 200
		
		async def _perform_scroll():
			if _PERSISTENT_PAGE:
				try:
					await _PERSISTENT_PAGE.evaluate(f"window.scrollBy({scroll_x}, {scroll_y})")
					await _update_state_and_map()
				except Exception as e:
					print(f"Scroll error: {e}")
					
		future = asyncio.run_coroutine_threadsafe(_perform_scroll(), get_background_loop())
		future.result()
		
		return '<html><head><meta http-equiv="refresh" content="0;url=/newnet-render"></head><body>Scrolling...</body></html>', 200

	if path == "/newnet-send-keystrokes" and req.method == "POST":
		x, y = int(request.form.get("x", 0)), int(request.form.get("y", 0))
		if typed_text := request.form.get("typed_text", ""):
			sync_interact(x, y, keystrokes=typed_text)
		return '<html><head><meta http-equiv="refresh" content="0;url=/newnet-render"></head><body>Typing...</body></html>', 200

	if path == "/" or path == "" or "index" in path:
		STREAMING_ENABLED = True
		sync_get_latest()
		return build_streaming_viewport(_LATEST_MAP_LINKS), 200

	return "Not Found", 404