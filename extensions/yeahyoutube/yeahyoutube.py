
import os
import json
import random
import string
import subprocess
import shutil
import time
import xml.etree.ElementTree as ET
from flask import request, send_file, render_template_string, Response, abort
from urllib.parse import urlparse, parse_qs
import requests
import config

DOMAIN = "youtube.com"
EXTENSION_DIR = os.path.dirname(os.path.abspath(__file__))
FLIM_DIRECTORY = os.path.join(EXTENSION_DIR, "flims")
DOWNLOAD_DIRECTORY = os.path.join(EXTENSION_DIR, "downloads")
SUBSCRIPTIONS_FILE = os.path.join(EXTENSION_DIR, "subscriptions.json")
PROFILE = "plus"

# Ensure directories exist
os.makedirs(FLIM_DIRECTORY, exist_ok=True)
os.makedirs(DOWNLOAD_DIRECTORY, exist_ok=True)

# Augment PATH to ensure subprocesses (ffmpeg, deno, node) are found
def augment_path():
	extra_paths = [
		"/opt/homebrew/bin",
		"/usr/local/bin",
		"/usr/bin",
		"/bin",
		"/usr/sbin",
		"/sbin",
		os.path.expanduser("~/.local/bin"),
	]
	
	# Try to locate NVM node binary path to add it if nvm is used
	nvm_dir = os.path.expanduser("~/.nvm/versions/node")
	if os.path.exists(nvm_dir):
		try:
			for version in sorted(os.listdir(nvm_dir), reverse=True):
				bin_path = os.path.join(nvm_dir, version, "bin")
				if os.path.exists(bin_path):
					extra_paths.append(bin_path)
					break
		except Exception:
			pass

	current_path = os.environ.get("PATH", "")
	split_paths = current_path.split(os.path.pathsep) if current_path else []
	paths_to_add = [p for p in extra_paths if p not in split_paths]
	if paths_to_add:
		os.environ["PATH"] = os.path.pathsep.join(paths_to_add) + (os.path.pathsep + current_path if current_path else "")

augment_path()

# Ensure yt-dlp-ejs (the JS challenge solver scripts) is installed
def ensure_ejs_installed():
	try:
		import importlib
		importlib.import_module('yt_dlp_ejs')
		print("[yeahyoutube] yt-dlp-ejs is installed")
	except ImportError:
		print("[yeahyoutube] yt-dlp-ejs not found, installing...")
		try:
			import sys
			subprocess.check_call(
				[sys.executable, '-m', 'pip', 'install', 'yt-dlp-ejs'],
				stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
			)
			print("[yeahyoutube] yt-dlp-ejs installed successfully")
		except Exception as e:
			print(f"[yeahyoutube] WARNING: Failed to install yt-dlp-ejs: {e}")
			print("[yeahyoutube] YouTube n-challenge solving may fail. Run: pip install 'yt-dlp[default]'")

ensure_ejs_installed()

def get_js_runtimes():
	runtimes = {}
	
	# 1. Check for deno
	deno_path = shutil.which("deno")
	if not deno_path:
		for p in ["/opt/homebrew/bin/deno", "/usr/local/bin/deno"]:
			if os.path.exists(p) and os.access(p, os.X_OK):
				deno_path = p
				break
	if deno_path:
		runtimes["deno"] = {"path": deno_path}
	else:
		runtimes["deno"] = {}
		
	# 2. Check for node
	node_path = shutil.which("node")
	if not node_path:
		# Check common mac/nvm paths
		possible_node_paths = ["/opt/homebrew/bin/node", "/usr/local/bin/node"]
		nvm_dir = os.path.expanduser("~/.nvm/versions/node")
		if os.path.exists(nvm_dir):
			try:
				for version in sorted(os.listdir(nvm_dir), reverse=True):
					bin_path = os.path.join(nvm_dir, version, "bin", "node")
					if os.path.exists(bin_path):
						possible_node_paths.append(bin_path)
			except Exception:
				pass
		for p in possible_node_paths:
			if os.path.exists(p) and os.access(p, os.X_OK):
				node_path = p
				break
	if node_path:
		runtimes["node"] = {"path": node_path}
	else:
		runtimes["node"] = {}

	# 3. Check for bun
	bun_path = shutil.which("bun")
	if not bun_path:
		for p in [os.path.expanduser("~/.bun/bin/bun"), "/opt/homebrew/bin/bun", "/usr/local/bin/bun"]:
			if os.path.exists(p) and os.access(p, os.X_OK):
				bun_path = p
				break
	if bun_path:
		runtimes["bun"] = {"path": bun_path}
	else:
		runtimes["bun"] = {}

	return runtimes

def get_cookie_file():
	"""
	Return the path to a cookie file for yt-dlp.
	
	yt-dlp will try to save cookies to this path on close, so the parent
	directory MUST exist and be writable. We check several locations in
	order of preference, and if none exist, we fall back to a guaranteed
	writable path inside the extension directory.
	"""
	# Production path (used on the actual Macproxy deployment)
	prod_cookies = '/DATA/AppData/macproxy_plus_os9_release/cookies.txt'
	if os.path.exists(prod_cookies):
		return prod_cookies
		
	# If the production directory exists but the file doesn't, we can use it
	# (yt-dlp will create the file on close)
	prod_dir = os.path.dirname(prod_cookies)
	if os.path.isdir(prod_dir):
		return prod_cookies
		
	# Local testing fallbacks - check for existing cookie files
	local_options = [
		os.path.join(EXTENSION_DIR, "cookies.txt"),
		os.path.join(EXTENSION_DIR, "www.youtube.com_cookies.txt"),
		os.path.expanduser("~/Downloads/www.youtube.com_cookies.txt"),
		os.path.expanduser("~/Downloads/www.youtube.com_cookies (1).txt"),
	]
	for p in local_options:
		if os.path.exists(p):
			return p
	
	# Fallback: use a cookie file inside the extension directory, which is
	# guaranteed to exist (we create FLIM_DIRECTORY and DOWNLOAD_DIRECTORY there).
	# This ensures yt-dlp has a writable path to save cookies on close.
	fallback_cookie = os.path.join(EXTENSION_DIR, "cookies.txt")
	print(f"[yeahyoutube] No cookie file found, using fallback: {fallback_cookie}")
	return fallback_cookie

# ---------------------------------------------------------------------------
# Subscriptions management
# ---------------------------------------------------------------------------

def load_subscriptions():
	"""Load subscriptions from the local JSON file."""
	if os.path.exists(SUBSCRIPTIONS_FILE):
		try:
			with open(SUBSCRIPTIONS_FILE, "r") as f:
				return json.load(f)
		except (json.JSONDecodeError, IOError) as e:
			print(f"[yeahyoutube] Error loading subscriptions: {e}")
	return {"subscriptions": []}

def save_subscriptions(data):
	"""Save subscriptions to the local JSON file."""
	try:
		with open(SUBSCRIPTIONS_FILE, "w") as f:
			json.dump(data, f, indent=2)
		return True
	except IOError as e:
		print(f"[yeahyoutube] Error saving subscriptions: {e}")
		return False

def import_newpipe_subscriptions(json_data):
	"""
	Import subscriptions from a NewPipe-format JSON export.
	NewPipe format: { "app_version": "...", "app_version_int": ..., "subscriptions": [{"service_id": 0, "url": "...", "name": "..."}, ...] }
	"""
	imported = []
	try:
		data = json.loads(json_data)
		subs = data.get("subscriptions", [])
		for sub in subs:
			url = sub.get("url", "")
			name = sub.get("name", "")
			# Extract channel ID or handle from URL
			# NewPipe URLs look like: https://www.youtube.com/channel/UC... or https://www.youtube.com/c/Handle
			channel_id = ""
			if "/channel/" in url:
				channel_id = url.split("/channel/")[-1].split("?")[0].split("/")[0]
			elif "/c/" in url:
				channel_id = url.split("/c/")[-1].split("?")[0].split("/")[0]
			elif "/user/" in url:
				channel_id = url.split("/user/")[-1].split("?")[0].split("/")[0]
			elif "@" in url:
				channel_id = url.split("@")[-1].split("?")[0].split("/")[0]
			
			if channel_id and name:
				imported.append({
					"name": name,
					"url": url,
					"channel_id": channel_id,
					"added": time.strftime("%Y-%m-%d")
				})
		
		if imported:
			current = load_subscriptions()
			# Merge, avoiding duplicates by URL
			existing_urls = {s["url"] for s in current.get("subscriptions", [])}
			for sub in imported:
				if sub["url"] not in existing_urls:
					current.setdefault("subscriptions", []).append(sub)
					existing_urls.add(sub["url"])
			save_subscriptions(current)
		
		return imported
	except (json.JSONDecodeError, KeyError) as e:
		print(f"[yeahyoutube] Error parsing NewPipe JSON: {e}")
		return []

def fetch_subscription_videos(channel_id, channel_name="Unknown", max_results=5):
	"""
	Fetch the latest videos from a channel using YouTube's RSS feed.
	No authentication or cookies needed.
	Only supports channel IDs (UC...).
	"""
	if not channel_id.startswith("UC"):
		print(f"[yeahyoutube] RSS feeds only work with channel IDs (UC...), got: {channel_id}")
		return []
	
	rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
	
	try:
		resp = requests.get(rss_url, timeout=15)
		resp.raise_for_status()
	except Exception as e:
		print(f"[yeahyoutube] Error fetching RSS feed for channel {channel_id}: {e}")
		return []
	
	try:
		root = ET.fromstring(resp.text)
	except ET.ParseError as e:
		print(f"[yeahyoutube] Error parsing RSS XML for channel {channel_id}: {e}")
		return []
	
	# RSS feed XML namespace
	ns = {
		'atom': 'http://www.w3.org/2005/Atom',
		'yt': 'http://www.youtube.com/xml/schemas/2015',
		'media': 'http://search.yahoo.com/mrss/',
	}
	
	entries = root.findall('atom:entry', ns)
	videos = []
	
	for entry in entries[:max_results]:
		video_id_elem = entry.find('yt:videoId', ns)
		if video_id_elem is None:
			continue
		video_id = video_id_elem.text
		
		title_elem = entry.find('atom:title', ns)
		title = title_elem.text if title_elem is not None else 'Untitled'
		
		# Get uploader from the author element
		author_elem = entry.find('atom:author', ns)
		uploader = channel_name
		if author_elem is not None:
			name_elem = author_elem.find('atom:name', ns)
			if name_elem is not None and name_elem.text:
				uploader = name_elem.text
		
		# Get description from media:group
		description = ''
		media_group = entry.find('media:group', ns)
		if media_group is not None:
			desc_elem = media_group.find('media:description', ns)
			if desc_elem is not None and desc_elem.text:
				description = desc_elem.text
		
		# Parse published date into YYYYMMDD format
		published_elem = entry.find('atom:published', ns)
		upload_date = ''
		if published_elem is not None and published_elem.text:
			# published format: 2024-01-01T00:00:00+00:00
			date_part = published_elem.text[:10]  # "2024-01-01"
			upload_date = date_part.replace('-', '')  # "20240101"
		
		videos.append({
			'id': video_id,
			'title': title,
			'uploader': uploader,
			'description': description,
			'view_count': 0,  # RSS feed doesn't include view counts
			'upload_date': upload_date,
		})
	
	return videos

def fetch_all_subscription_videos(max_per_channel=3):
	"""Fetch latest videos from all subscribed channels via RSS feeds."""
	subs_data = load_subscriptions()
	subs = subs_data.get("subscriptions", [])
	
	all_videos = []
	for sub in subs:
		channel_id = sub.get("channel_id", "")
		name = sub.get("name", "Unknown")
		if channel_id:
			videos = fetch_subscription_videos(channel_id, name, max_per_channel)
			all_videos.extend(videos)
	
	# Sort by upload date (newest first), handling missing dates
	def sort_key(v):
		return v.get('upload_date', '00000000')
	all_videos.sort(key=sort_key, reverse=True)
	
	return all_videos

# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_homepage():
	"""Generate the main homepage with search, subscriptions link, and settings."""
	return render_template_string('''
	<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
	<html>
		<head>
			<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
			<title>Yeah! YouTube - Broadcast Yourself</title>
		</head>
		<body>
			<center>
<pre>
                                                    
  ##      ##         ########     ##
   ##    ##             ##        ##
    ##  ## ####  ##  ## ## ##  ## #####   ####
     #### ##  ## ##  ## ## ##  ## ##  ## ##  ##
      ##  ##  ## ##  ## ## ##  ## ##  ## ######
      ##  ##  ## ##  ## ## ##  ## ##  ## ##
YEAH! ##   ####   ##### ##  ##### #####   #####
<br>
</pre>
				<form method="get" action="/results">
					<input type="text" size="40" name="search_query">
					<input type="submit" value="Search">
				</form>
				<br>
				<font size="3">
					<a href="/subscriptions">[My Subscriptions]</a>
					&nbsp;&nbsp;
					<a href="/settings">[Settings]</a>
				</font>
			</center>
			<hr>
		</body>
	</html>
	''')

def generate_search_results(search_results, query):
	videos_html = generate_search_results_html(search_results)
	return render_template_string('''
	<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
	<html>
		<head>
			<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
			<title>Yeah! YouTube - Search Results for {{ query }}</title>
		</head>
		<body>
			<form method="get" action="/results">
				<input type="text" size="40" name="search_query" value="{{ query }}">
				<input type="submit" value="Search">
			</form>
			<hr>
			{{ videos_html|safe }}
		</body>
	</html>
	''', videos_html=videos_html, query=query)

def generate_search_results_html(videos):
	html = ''
	for video in videos:
		video_id = video.get('id')
		if not video_id:
			continue
		url = f"https://www.{DOMAIN}/watch?v={video_id}"
		title = video.get('title', 'Untitled')
		creator = video.get('uploader', 'Unknown creator')
		description = video.get('description', '')

		# Handle description formatting
		if description:
			if len(description) > 200:
				formatted_description = f"{description[:200]}..."
			else:
				formatted_description = description
		else:
			formatted_description = "..."

		html += f'''
		<b><a href="{url}">{title}</a></b><br>
		<font size="2">
			<b>{creator}</b><br>
			{formatted_description}
		</font>
		<br><br>
		'''
	return html

def generate_subscriptions_page(videos, subscriptions):
	"""Generate the subscriptions page showing latest videos from subscribed channels."""
	html = '''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Yeah! YouTube - My Subscriptions</title>
</head>
<body>
<center><h1>My Subscriptions</h1></center>
<hr>
<font size="2"><a href="/">[Back to Home]</a> | <a href="/settings">[Settings]</a></font>
<br><br>
'''

	if not subscriptions:
		html += '<p>No subscriptions yet. Go to <a href="/settings">Settings</a> to import your NewPipe subscriptions.</p>\n'
		html += '</body>\n</html>'
		return html

	# Show subscribed channels
	html += '<b>Subscribed Channels:</b><br>\n'
	for sub in subscriptions:
		name = sub.get("name", "Unknown")
		url = sub.get("url", "")
		html += f'<font size="2">- <a href="{url}">{name}</a></font><br>\n'
	html += '<br><hr>\n'

	# Show latest videos
	if videos:
		html += '<b>Latest Videos:</b><br><br>\n'
		for video in videos:
			video_id = video.get('id')
			if not video_id:
				continue
			video_url = f"https://www.{DOMAIN}/watch?v={video_id}"
			title = video.get('title', 'Untitled')
			creator = video.get('uploader', 'Unknown')
			upload_date = video.get('upload_date', '')
			view_count = video.get('view_count', 0)

			# Format date from YYYYMMDD to YYYY-MM-DD
			formatted_date = upload_date
			if len(upload_date) == 8:
				formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

			html += f'''
<b><a href="{video_url}">{title}</a></b><br>
<font size="2">
<b>{creator}</b> | {formatted_date} | {view_count} views<br>
</font>
<br>
'''
	else:
		html += '<p>No videos found from your subscriptions. They may not have uploaded recently.</p>\n'

	html += '<hr>\n'
	html += '<center><font size="2">Yeah! YouTube - Subscriptions</font></center>\n'
	html += '</body>\n</html>'
	return html

def generate_settings_page():
	"""Generate the settings page with NewPipe JSON import form."""
	html = '''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Yeah! YouTube - Settings</title>
</head>
<body>
<center><h1>Settings</h1></center>
<hr>
<font size="2"><a href="/">[Back to Home]</a> | <a href="/subscriptions">[My Subscriptions]</a></font>
<br><br>
'''

	# Show current subscriptions count
	subs_data = load_subscriptions()
	subs = subs_data.get("subscriptions", [])
	html += f'<b>Current Subscriptions:</b> {len(subs)} channel(s)<br>\n'
	if subs:
		html += '<ul>\n'
		for sub in subs:
			html += f'<li><font size="2">{sub.get("name", "Unknown")}</font></li>\n'
		html += '</ul>\n'
	html += '<br>\n'

	# Import form - uses a simple form with a textarea for pasting NewPipe JSON
	html += '''<b>Import NewPipe Subscriptions:</b><br>
<font size="2">
Paste the contents of your NewPipe subscriptions export JSON file below.<br>
The file can be exported from NewPipe via Settings > Content > Export subscriptions.<br>
</font>
<br>
<form method="post" action="/import_subscriptions">
<textarea name="newpipe_json" rows="15" cols="60" wrap="off"></textarea>
<br><br>
<input type="submit" value="Import Subscriptions">
</form>
<br>
<hr>
<font size="2">
<b>How to export from NewPipe:</b><br>
1. Open NewPipe on your Android device<br>
2. Go to Settings > Content<br>
3. Tap "Export subscriptions"<br>
4. Share or save the .json file<br>
5. Open the file in a text editor, copy all the text<br>
6. Paste it into the text area above and click Import<br>
</font>
<br>
<hr>
<center><font size="2">Yeah! YouTube - Settings</font></center>
</body>
</html>'''
	return html

def generate_import_result_page(imported_count, failed=False):
	"""Generate the import result page."""
	if failed:
		html = '''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Yeah! YouTube - Import Failed</title>
</head>
<body>
<center><h1>Import Failed</h1></center>
<hr>
<p>Could not parse the provided JSON. Please ensure you are pasting a valid NewPipe subscriptions export file.</p>
<br>
<a href="/settings">[Back to Settings]</a>
</body>
</html>'''
	else:
		html = f'''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Yeah! YouTube - Import Successful</title>
</head>
<body>
<center><h1>Import Successful</h1></center>
<hr>
<p>Successfully imported {imported_count} subscription(s).</p>
<br>
<a href="/subscriptions">[View My Subscriptions]</a>
<br>
<a href="/settings">[Back to Settings]</a>
</body>
</html>'''
	return html

# ---------------------------------------------------------------------------
# Video operations - Streaming instead of download
# ---------------------------------------------------------------------------

def transcode_video(video_id):
	"""
	Download a YouTube video and transcode it to a QuickTime-compatible .mov file
	for progressive download / streaming on Mac OS 9 browsers.
	
	Returns the path to the transcoded .mov file, or None on failure.
	"""
	flim_path = os.path.join(FLIM_DIRECTORY, f"{video_id}.mov")
	
	# If the transcoded file already exists, return it immediately (cache hit)
	if os.path.exists(flim_path):
		print(f"[yeahyoutube] Cache hit for video {video_id}")
		return flim_path
	
	video_url = f"https://invidious.nerdvpn.de/watch?v={video_id}"
	
	ydl_opts = {
		'outtmpl': os.path.join(DOWNLOAD_DIRECTORY, f"{video_id}.%(ext)s"),
		'noplaylist': True,
		'verbose': True,
		'js_runtimes': get_js_runtimes(),
		'remote_components': ['ejs:github'],
	}

	downloaded_video_path = None
	with yt_dlp.YoutubeDL(ydl_opts) as ydl:
		try:
			info_dict = ydl.extract_info(video_url, download=True)
			downloaded_video_path = ydl.prepare_filename(info_dict)
		except Exception as e:
			print(f"[yeahyoutube] Error downloading video: {e}")
			return None
			
	if not downloaded_video_path or not os.path.exists(downloaded_video_path):
		print(f"[yeahyoutube] Failed to download video {video_id}")
		return None

	try:
		subprocess.run([
			"ffmpeg",
			"-y",  # Overwrite output file if it exists (we already checked, but safe)
			"-i", downloaded_video_path,
			"-f", "mov",
			"-movflags", "faststart",  # Enable QuickTime progressive download / streaming
			"-vcodec", "svq1",  # Sorenson Video codec (best Mac OS 9 QuickTime compatibility)
			"-acodec", "adpcm_ima_qt",  # IMA ADPCM audio (best Mac OS 9 QuickTime compatibility)
			"-ar", "11025",  # Low audio sample rate for 56k
			"-ac", "1",  # Mono audio
			"-vf", "scale=300:225",  # Low resolution for Mac OS 9 screens
			"-r", "12",  # Low frame rate for 56k modem
			"-b:v", "74k",  # Very low video bitrate for 56k
			"-b:a", "4k",  # Low audio bitrate
			"-q:v", "5",  # Slightly lower quality
			flim_path
		], check=True, capture_output=True, text=True)
		print(f"[yeahyoutube] Successfully transcoded video {video_id} to {flim_path}")
	except subprocess.CalledProcessError as e:
		print(f"[yeahyoutube] ffmpeg error: {e.stderr}")
		return None
	finally:
		# Clean up the downloaded source file
		if downloaded_video_path and os.path.exists(downloaded_video_path):
			os.remove(downloaded_video_path)
			print(f"[yeahyoutube] Cleaned up source file {downloaded_video_path}")

	if os.path.exists(flim_path):
		return flim_path
	else:
		print(f"[yeahyoutube] Transcoded file not found at {flim_path}")
		return None


def stream_video(video_id):
	"""
	Serve a transcoded .mov file with HTTP Range support for QuickTime
	progressive download (streaming).
	"""
	flim_path = transcode_video(video_id)
	if not flim_path:
		return abort(500, "Error: Failed to transcode video")
	
	file_size = os.path.getsize(flim_path)
	
	# Handle HTTP Range requests (required for QuickTime progressive download)
	range_header = request.headers.get('Range', None)
	
	if range_header:
		# Parse the Range header: "bytes=<start>-<end>"
		byte_range = range_header.strip().split('bytes=')[1]
		range_start, range_end = byte_range.split('-')
		range_start = int(range_start) if range_start else 0
		range_end = int(range_end) if range_end else file_size - 1
		
		# If no end was specified, send from start to end of file
		if range_end >= file_size:
			range_end = file_size - 1
		
		length = range_end - range_start + 1
		
		# Read the requested byte range
		with open(flim_path, 'rb') as f:
			f.seek(range_start)
			data = f.read(length)
		
		response = Response(
			data,
			status=206,  # Partial Content
			mimetype='video/quicktime',
			headers={
				'Content-Range': f'bytes {range_start}-{range_end}/{file_size}',
				'Content-Length': str(length),
				'Accept-Ranges': 'bytes',
			}
		)
		return response
	else:
		# No Range header - send the entire file
		return send_file(
			flim_path,
			mimetype='video/quicktime',
			as_attachment=False,  # Do NOT force download - let the browser/QuickTime handle it
			download_name=f"{video_id}.mov"
		)


def generate_watch_page(video_id, video_info=None):
	"""
	Generate a watch page with an embedded QuickTime player.
	Uses HTML 3.2-compatible <embed> tag for maximum compatibility
	with IE5 and Netscape Navigator on Mac OS 9.
	"""
	title = video_info.get('title', 'Video') if video_info else 'Video'
	creator = video_info.get('uploader', 'Unknown') if video_info else 'Unknown'
	description = video_info.get('description', '') if video_info else ''
	
	# Format description for display
	if description:
		if len(description) > 300:
			formatted_description = f"{description[:300]}..."
		else:
			formatted_description = description
	else:
		formatted_description = ""
	
	# Stream URL - use the youtube.com domain so the proxy routes it through this extension
	stream_url = f"https://www.{DOMAIN}/stream/{video_id}.mov"
	
	html = f'''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Yeah! YouTube - {title}</title>
</head>
<body>
<center>
<h2>{title}</h2>
<br>
<b>{creator}</b>
<br><br>
<embed src="{stream_url}" type="video/quicktime" width="300" height="225" autoplay="true" controller="true" pluginspage="http://www.apple.com/quicktime/download/">
<br><br>
<font size="2">
<a href="/">[Back to Home]</a>
</font>
</center>
<hr>
<font size="2">
{formatted_description}
</font>
<hr>
<center><font size="2">Yeah! YouTube - Video Player</font></center>
</body>
</html>'''
	return html


def fetch_video_info(video_id):
	"""
	Fetch metadata for a single video using yt-dlp (without downloading).
	"""
	video_url = f"https://invidious.nerdvpn.de/watch?v={video_id}"
	
	ydl_opts = {
		'verbose': False,
		'quiet': True,
		'noplaylist': True,
		'skip_download': True,
		'cookiefile': get_cookie_file(),
		'js_runtimes': get_js_runtimes(),
		'remote_components': ['ejs:github'],
		'extractor_args': {'youtube': {'player-client': ['default','mweb']}}
	}
	
	with yt_dlp.YoutubeDL(ydl_opts) as ydl:
		try:
			info = ydl.extract_info(video_url, download=False)
			return {
				'id': info.get('id', video_id),
				'title': info.get('title', 'Untitled'),
				'uploader': info.get('uploader', 'Unknown'),
				'description': info.get('description', ''),
				'view_count': info.get('view_count', 0),
				'upload_date': info.get('upload_date', ''),
			}
		except Exception as e:
			print(f"[yeahyoutube] Error fetching video info: {e}")
			return None


def search_videos(query):
	ydl_opts = {
		'verbose': True,
		'default_search': 'ytsearch10',  # search for 10 videos
		'noplaylist': True,
		'skip_download': True,
		'extract_flat': 'in_playlist',
		'cookiefile': get_cookie_file(),
		'js_runtimes': get_js_runtimes(),
		'remote_components': ['ejs:github'],
		'extractor_args': {'youtube': {'player-client': ['default','mweb']}}
	}
	with yt_dlp.YoutubeDL(ydl_opts) as ydl:
		try:
			search_results = ydl.extract_info(query, download=False)
			return search_results.get('entries', [])
		except Exception as e:
			print(f"Error searching youtube: {e}")
			return []

# ---------------------------------------------------------------------------
# Main request handler
# ---------------------------------------------------------------------------

def handle_request(req):
	parsed_url = urlparse(req.url)
	path = parsed_url.path
	query_params = parse_qs(parsed_url.query)

	# /watch?v=ID - Show the watch page with embedded QuickTime player
	if path == "/watch" and 'v' in query_params:
		video_id = query_params['v'][0]
		# Fetch video info for the watch page
		video_info = fetch_video_info(video_id)
		return generate_watch_page(video_id, video_info), 200
	
	# /stream/ID.mov - Stream the transcoded video with Range support
	elif path.startswith("/stream/") and path.endswith(".mov"):
		video_id = path[len("/stream/"):-len(".mov")]
		if not video_id:
			return "Error: No video ID specified", 400
		return stream_video(video_id)
	
	elif path == "/results" and 'search_query' in query_params:
		query = query_params['search_query'][0]
		search_results = search_videos(query)
		return generate_search_results(search_results, query), 200
	
	elif path == "/subscriptions":
		subs_data = load_subscriptions()
		subs = subs_data.get("subscriptions", [])
		videos = fetch_all_subscription_videos(max_per_channel=3)
		return generate_subscriptions_page(videos, subs), 200
	
	elif path == "/settings":
		return generate_settings_page(), 200
	
	elif path == "/import_subscriptions" and req.method == "POST":
		newpipe_json = req.form.get("newpipe_json", "")
		if not newpipe_json.strip():
			return generate_import_result_page(0, failed=True), 400
		imported = import_newpipe_subscriptions(newpipe_json)
		if imported:
			return generate_import_result_page(len(imported)), 200
		else:
			return generate_import_result_page(0, failed=True), 400
	
	else:
		return generate_homepage(), 200
