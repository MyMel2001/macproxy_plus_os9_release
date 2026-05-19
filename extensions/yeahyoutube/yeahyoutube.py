
import os
import json
import random
import string
import subprocess
import shutil
from flask import request, send_file, render_template_string
from urllib.parse import urlparse, parse_qs
import yt_dlp
import config

DOMAIN = "youtube.com"
EXTENSION_DIR = os.path.dirname(os.path.abspath(__file__))
FLIM_DIRECTORY = os.path.join(EXTENSION_DIR, "flims")
DOWNLOAD_DIRECTORY = os.path.join(EXTENSION_DIR, "downloads")
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
	prod_cookies = '/DATA/AppData/macproxy_plus_os9_release/cookies.txt'
	if os.path.exists(prod_cookies):
		return prod_cookies
		
	# Local testing fallbacks
	local_options = [
		os.path.join(EXTENSION_DIR, "cookies.txt"),
		os.path.join(EXTENSION_DIR, "www.youtube.com_cookies.txt"),
		os.path.expanduser("~/Downloads/www.youtube.com_cookies.txt"),
		os.path.expanduser("~/Downloads/www.youtube.com_cookies (1).txt"),
	]
	for p in local_options:
		if os.path.exists(p):
			return p
			
	return prod_cookies # Default back to production path if none exist

def generate_homepage():
	return render_template_string('''
	<!DOCTYPE html>
	<html lang="en">
		<head>
			<meta charset="UTF-8">
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
					<input type="text" size="40" name="search_query" required style="font-size: 42px;">
					<input type="submit" value="Search">
				</form>
				<br>
			</center>
			<hr>
		</body>
	</html>
	''')

def generate_search_results(search_results, query):
	videos_html = generate_search_results_html(search_results)
	return render_template_string('''
	<!DOCTYPE html>
	<html lang="en">
		<head>
			<meta charset="UTF-8">
			<title>Yeah! YouTube - Search Results for {{ query }}</title>
		</head>
		<body>
			<form method="get" action="/results">
				<input type="text" size="40" name="search_query" value="{{ query }}" required style="font-size: 16px;">
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

def handle_video_request(video_id):
	# Download the video using yt-dlp
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
			print(f"Error downloading video: {e}")
			return "Error downloading video", 500
			
	if not downloaded_video_path or not os.path.exists(downloaded_video_path):
		return "Error: Failed to download video", 500

	flim_path = os.path.join(FLIM_DIRECTORY, f"{video_id}.mov")
	
	try:
		subprocess.run([
			"ffmpeg",
			"-n", # dont overwrite output file if it exists
			"-i", downloaded_video_path,
			"-f", "mov",
			"-c:v", "mjpeg",  # Motion JPEG (extremely fast, compatible with QuickTime 2.0+)
			"-c:a", "adpcm_ima_qt",  # ADPCM audio (compatible with QT 2.1+)
			"-ar", "22050",  # 22kHz sample rate
			"-ac", "1",  # Mono audio
			"-vf", "scale=320:240",  # 320x240 resolution
			"-r", "15",  # 15 fps
			"-q:v", "7",  # Quality setting for MJPEG (lower is better, 7 is a good balance for file size/quality)
			flim_path
		], check=True, capture_output=True, text=True)
	except subprocess.CalledProcessError as e:
		print(f"ffmpeg error: {e.stderr}")
		return "Error generating video", 500
	finally:
		# Clean up the downloaded file
		if os.path.exists(downloaded_video_path):
			os.remove(downloaded_video_path)

	if os.path.exists(flim_path):
		return send_file(flim_path, as_attachment=True, download_name=f"{video_id}.mov")
	else:
		return "Error: File not generated", 500

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

def handle_request(req):
	parsed_url = urlparse(req.url)
	path = parsed_url.path
	query_params = parse_qs(parsed_url.query)

	if path == "/watch" and 'v' in query_params:
		video_id = query_params['v'][0]
		return handle_video_request(video_id)
	elif path == "/results" and 'search_query' in query_params:
		query = query_params['search_query'][0]
		search_results = search_videos(query)
		return generate_search_results(search_results, query), 200
	else:
		return generate_homepage(), 200
