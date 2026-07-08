"""
GitHub extension for Macproxy
Shows repositories, downloads ZIPs, manages Issues/PRs, and supports uploading 
ZIP changes to create Pull Requests via standard HTML forms.
Compatible with 1998-era browsers (IE5, Netscape Navigator).
"""

import os
import json
import io
import zipfile
import requests
import subprocess
import shutil
import time
from flask import request, Response, send_file
from urllib.parse import urlparse, parse_qs

DOMAIN = "github.com"
EXTENSION_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_CACHE_DIR = os.path.join(EXTENSION_DIR, "repo_cache")
WORK_DIR = os.path.join(EXTENSION_DIR, "github_work")

os.makedirs(REPO_CACHE_DIR, exist_ok=True)
os.makedirs(WORK_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
GITHUB_API_BASE = "https://api.github.com"


def get_github_users():
	"""Get the list of GitHub users from config."""
	try:
		import config
		return getattr(config, 'GITHUB_USERS', [])
	except (ImportError, AttributeError):
		return []


def run_gh_cmd(args):
	"""Helper to run a GitHub CLI command and return output."""
	try:
		result = subprocess.run(["gh"] + args, capture_output=True, text=True, check=True)
		return result.stdout
	except Exception as e:
		print(f"[github] gh command error: {e}")
		return ""


def fetch_user_repos(username):
	"""Fetch public repositories for a given GitHub username."""
	url = f"{GITHUB_API_BASE}/users/{username}/repos?per_page=100&sort=updated"
	headers = {
		"User-Agent": USER_AGENT,
		"Accept": "application/vnd.github.v3+json",
	}
	try:
		resp = requests.get(url, headers=headers, timeout=15)
		if resp.status_code == 200:
			return resp.json()
		print(f"[github] Error fetching repos for {username}: HTTP {resp.status_code}")
		return []
	except Exception as e:
		print(f"[github] Exception fetching repos for {username}: {e}")
		return []


def download_repo_zip(repo_full_name, branch="master"):
	"""Download a repository as a ZIP archive and return the bytes."""
	cache_key = repo_full_name.replace("/", "_") + ".zip"
	cache_path = os.path.join(REPO_CACHE_DIR, cache_key)

	if os.path.exists(cache_path):
		age = os.path.getmtime(cache_path)
		if time.time() - age < 3600:
			with open(cache_path, "rb") as f:
				return f.read()

	for branch_attempt in ["master", "main"]:
		zip_url = f"https://github.com/{repo_full_name}/archive/refs/heads/{branch_attempt}.zip"
		headers = {"User-Agent": USER_AGENT}
		try:
			resp = requests.get(zip_url, headers=headers, timeout=30)
			if resp.status_code == 200:
				with open(cache_path, "wb") as f:
					f.write(resp.content)
				return resp.content
		except Exception as e:
			print(f"[github] Error downloading {repo_full_name} ({branch_attempt}): {e}")

	return None


def handle_zip_pr(repo_full, zip_file, pr_title, pr_body):
	"""Clones repo, processes zip contents (including deletions via .DEL), and creates a PR."""
	repo_name = repo_full.split("/")[-1]
	timestamp = int(time.time())
	local_clone_path = os.path.join(WORK_DIR, f"{repo_name}_{timestamp}")
	branch_name = f"patch-{timestamp}"

	try:
		# 1. Clone the repo
		repo_url = f"https://github.com/{repo_full}.git"
		subprocess.run(["git", "clone", repo_url, local_clone_path], check=True, capture_output=True)
		
		# 2. Create and checkout new branch
		subprocess.run(["git", "checkout", "-b", branch_name], cwd=local_clone_path, check=True, capture_output=True)

		# 3. Process the uploaded ZIP file
		zip_bytes = io.BytesIO(zip_file.read())
		with zipfile.ZipFile(zip_bytes, 'r') as z:
			# Track deletions first to handle .DEL overrides securely
			del_files = [f for f in z.namelist() if f.endswith('.DEL')]
			
			for f_info in z.infolist():
				if f_info.is_dir() or f_info.filename.endswith('.DEL'):
					continue
				
				# Write out standard files (overwriting existing ones safely)
				target_path = os.path.join(local_clone_path, f_info.filename)
				os.makedirs(os.path.dirname(target_path), exist_ok=True)
				with open(target_path, "wb") as out_f:
					out_f.write(z.read(f_info.filename))

			# Perform explicit deletions requested by .DEL placeholders
			for del_file in del_files:
				target_to_delete = del_file[:-4]  # Remove '.DEL' extension
				full_del_path = os.path.join(local_clone_path, target_to_delete)
				if os.path.exists(full_del_path):
					if os.path.isdir(full_del_path):
						shutil.rmtree(full_del_path)
					else:
						os.remove(full_del_path)

		# 4. Commit and Push
		subprocess.run(["git", "add", "."], cwd=local_clone_path, check=True, capture_output=True)
		subprocess.run(["git", "commit", "-m", pr_title], cwd=local_clone_path, check=True, capture_output=True)
		subprocess.run(["git", "push", "origin", branch_name], cwd=local_clone_path, check=True, capture_output=True)

		# 5. Create PR using GitHub CLI
		pr_cmd = ["gh", "pr", "create", "-R", repo_full, "-B", "main", "-H", branch_name, "-t", pr_title, "-b", pr_body]
		# Fallback attempt to master if main branch config isn't absolute
		res = subprocess.run(pr_cmd, cwd=local_clone_path, capture_output=True, text=True)
		if res.returncode != 0:
			pr_cmd[5] = "master"
			res = subprocess.run(pr_cmd, cwd=local_clone_path, capture_output=True, text=True)

		return res.returncode == 0
	except Exception as e:
		print(f"[github] PR creation failed: {e}")
		return False
	finally:
		if os.path.exists(local_clone_path):
			shutil.rmtree(local_clone_path)


def generate_homepage(users_repos, authed_user):
	"""Generate the main GitHub repos page with retro-browser compatibility."""
	html = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<head>\n<title>GitHub Repo Browser</title>\n</head>\n<body>\n'
	html += '<center><h1>GitHub Repo Browser</h1></center>\n<hr>\n'

	if not users_repos:
		html += '<p>No GitHub users configured. Add GITHUB_USERS to config.py.</p>\n</body>\n</html>'
		return html

	for username, repos in users_repos:
		is_owner = (username.lower() == authed_user.lower())
		html += f'<h2>Repositories by <a href="https://github.com/{username}">{username}</a></h2>\n<ul>\n'
		
		for repo in repos:
			repo_name = repo.get("name", "unknown")
			repo_full = repo.get("full_name", f"{username}/{repo_name}")
			description = repo.get("description") or "No description"
			lang = repo.get("language") or "N/A"
			stars = repo.get("stargazers_count", 0)

			html += '<li>\n'
			html += f'<b><a href="https://github.com/{repo_full}">{repo_name}</a></b><br>\n'
			html += f'<font size="2">{description}<br>Language: {lang} | Stars: {stars}<br>\n'
			html += f'<a href="/download?repo={repo_full}">[Download ZIP]</a>'
			
			if is_owner:
				html += f' | <a href="/repo-manage?repo={repo_full}"><b>[Manage Issues & PRs]</b></a>'
			
			html += '</font>\n<br><br></li>\n'
		html += '</ul>\n<hr>\n'

	html += '<center><font size="2">GitHub Repo Browser for Macproxy</font></center>\n</body>\n</html>'
	return html


def generate_manage_page(repo_full):
	"""Generates the single-repo overview dashboard for Issues and Pull Requests."""
	html = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<head>\n'
	html += f'<title>Manage {repo_full}</title>\n</head>\n<body>\n'
	html += f'<p><a href="/">&lt;&lt; Back to Home</a></p>\n'
	html += f'<h1>Manage Repository: {repo_full}</h1>\n<hr>\n'

	# --- PUSH ZIP TO PR FORM ---
	html += '<h2>Upload Code Update (Create PR)</h2>\n'
	html += f'<form action="/upload-pr?repo={repo_full}" method="POST" enctype="multipart/form-data">\n'
	html += '<table border="0">\n'
	html += '<tr><td><b>ZIP File:</b></td><td><input type="file" name="zip_file"></td></tr>\n'
	html += '<tr><td><b>PR Title:</b></td><td><input type="text" name="title" size="40"></td></tr>\n'
	html += '<tr><td><b>Description:</b></td><td><textarea name="body" rows="3" cols="40"></textarea></td></tr>\n'
	html += '<tr><td></td><td><input type="submit" value="Submit Pull Request"></td></tr>\n'
	html += '</table>\n</form>\n<font size="2" color="#555555">* Note: Existing files are overwritten. Append a .DEL file extension inside your archive to flag a file for safe elimination.</font>\n<hr>\n'

	# --- PULL REQUESTS OVERVIEW ---
	html += '<h2>Pull Requests</h2>\n'
	prs_json = run_gh_cmd(["pr", "list", "-R", repo_full, "--json", "number,title,author,state"])
	try:
		prs = json.loads(prs_json) if prs_json else []
	except:
		prs = []

	if not prs:
		html += '<p>No open Pull Requests found.</p>\n'
	else:
		html += '<table border="1" cellpadding="4" cellspacing="0">\n<tr bgcolor="#EEEEEE"><td><b>#</b></td><td><b>Title</b></td><td><b>Author</b></td><td><b>Action</b></td></tr>\n'
		for pr in prs:
			num = pr.get("number")
			html += f'<tr><td>{num}</td><td><a href="/pr-detail?repo={repo_full}&num={num}">{pr.get("title")}</a></td><td>{pr.get("author", {}).get("login")}</td>\n'
			html += f'<td><form action="/pr-action?repo={repo_full}&num={num}" method="POST" style="margin:0;">'
			html += '<input type="submit" name="action" value="Approve & Merge"> '
			html += '<input type="submit" name="action" value="Close Pr">'
			html += '</form></td></tr>\n'
		html += '</table>\n'
	html += '<hr>\n'

	# --- ISSUES OVERVIEW ---
	html += '<h2>Issues</h2>\n'
	issues_json = run_gh_cmd(["issue", "list", "-R", repo_full, "--json", "number,title,author"])
	try:
		issues = json.loads(issues_json) if issues_json else []
	except:
		issues = []

	if not issues:
		html += '<p>No open Issues found.</p>\n'
	else:
		html += '<table border="1" cellpadding="4" cellspacing="0">\n<tr bgcolor="#EEEEEE"><td><b>#</b></td><td><b>Title</b></td><td><b>Author</b></td></tr>\n'
		for issue in issues:
			num = issue.get("number")
			html += f'<tr><td>{num}</td><td><a href="/issue-detail?repo={repo_full}&num={num}">{issue.get("title")}</a></td><td>{issue.get("author", {}).get("login")}</td></tr>\n'
		html += '</table>\n'

	html += '</body>\n</html>'
	return html


def generate_item_detail(repo_full, item_type, num):
	"""Generates feedback view containing comments, responses, and closure parameters."""
	label = "Pull Request" if item_type == "pr" else "Issue"
	cmd_type = "pr" if item_type == "pr" else "issue"
	
	view_json = run_gh_cmd([cmd_type, "view", str(num), "-R", repo_full, "--json", "number,title,body,comments"])
	try:
		item = json.loads(view_json) if view_json else {}
	except:
		item = {}

	html = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<body>\n'
	html += f'<p><a href="/repo-manage?repo={repo_full}">&lt;&lt; Back to Management</a></p>\n'
	html += f'<h1>{label} #{num}: {item.get("title", "Unknown")}</h1>\n'
	html += f'<blockquote bgcolor="#F5F5F5"><pre>{item.get("body") or "No description provided."}</pre></blockquote>\n<hr>\n'

	html += '<h3>Comments</h3>\n'
	comments = item.get("comments", [])
	if not comments:
		html += '<p>No comments yet.</p>\n'
	else:
		for c in comments:
			html += f'<p><b>{c.get("author", {}).get("login")}</b> commented:<br>\n'
			html += f'<font size="2">{c.get("body")}</font></p><hr width="50%" align="left">\n'

	# Interactive Comment Interface
	html += f'<h3>Add Comment</h3>\n'
	html += f'<form action="/add-comment?repo={repo_full}&num={num}&type={item_type}" method="POST">\n'
	html += '<textarea name="comment" rows="4" cols="50"></textarea><br>\n'
	html += '<input type="submit" value="Post Comment">\n'
	html += '</form>\n'

	html += '</body>\n</html>'
	return html


def handle_request(req):
	"""Main request router tailored for standard HTML browser environments."""
	parsed_url = urlparse(req.url)
	path = parsed_url.path
	query_params = parse_qs(parsed_url.query)

	# Identify active account profile through the gh configuration context
	authed_user = run_gh_cmd(["api", "user", "--jq", ".login"]).strip()

	# 1. Download Handler
	if path == "/download" and "repo" in query_params:
		repo_full = query_params["repo"][0]
		zip_data = download_repo_zip(repo_full)
		if zip_data:
			repo_name = repo_full.split("/")[-1]
			return send_file(io.BytesIO(zip_data), as_attachment=True, download_name=f"{repo_name}.zip", mimetype="application/zip")
		return Response("Error downloading repository.", status=500)

	# 2. Management Gateway View
	if path == "/repo-manage" and "repo" in query_params:
		repo_full = query_params["repo"][0]
		return generate_manage_page(repo_full), 200

	# 3. Zip Payload Intake Action via standard POST forms
	if path == "/upload-pr" and req.method == "POST":
		repo_full = query_params["repo"][0]
		zip_file = req.files.get("zip_file")
		title = req.form.get("title", "Code Update via Macproxy")
		body = req.form.get("body", "Imported from Vintage Environment Workflow.")
		
		if zip_file and handle_zip_pr(repo_full, zip_file, title, body):
			return f'<html><body><h2>PR successfully launched!</h2><p><a href="/repo-manage?repo={repo_full}">Return to management cockpit</a></p></body></html>', 200
		return '<html><body><h2>Operation Failed</h2><p>Verify git authentication logs or structural integrity of payload.</p></body></html>', 500

	# 4. Pull Request State Mutators
	if path == "/pr-action" and req.method == "POST":
		repo_full = query_params["repo"][0]
		num = query_params["num"][0]
		action = req.form.get("action")

		if action == "Approve & Merge":
			run_gh_cmd(["pr", "review", num, "-R", repo_full, "--approve"])
			run_gh_cmd(["pr", "merge", num, "-R", repo_full, "--merge", "--delete-branch"])
		elif action == "Close Pr":
			run_gh_cmd(["pr", "close", num, "-R", repo_full])
		return f'<html><head><meta http-equiv="refresh" content="1;url=/repo-manage?repo={repo_full}"></head><body>Processing operation...</body></html>', 200

	# 5. Detail Inspection Dashboards
	if path in ["/pr-detail", "/issue-detail"]:
		repo_full = query_params["repo"][0]
		num = query_params["num"][0]
		item_type = "pr" if path == "/pr-detail" else "issue"
		return generate_item_detail(repo_full, item_type, num), 200

	# 6. Message Thread Appenders
	if path == "/add-comment" and req.method == "POST":
		repo_full = query_params["repo"][0]
		num = query_params["num"][0]
		item_type = query_params["type"][0]
		comment_text = req.form.get("comment", "")
		cmd_type = "pr" if item_type == "pr" else "issue"

		if comment_text:
			run_gh_cmd([cmd_type, "comment", num, "-R", repo_full, "-b", comment_text])
		return f'<html><head><meta http-equiv="refresh" content="1;url=/{cmd_type}-detail?repo={repo_full}&num={num}"></head><body>Posting Comment...</body></html>', 200

	# Home view
	users = get_github_users()
	users_repos = []
	for username in users:
		repos = fetch_user_repos(username)
		if repos:
			users_repos.append((username, repos))

	return generate_homepage(users_repos, authed_user), 200