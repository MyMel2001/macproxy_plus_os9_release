"""
GitHub extension for Macproxy
Shows repositories from configured GitHub users and allows downloading them as ZIP archives.
Compatible with 1998-era browsers (IE5, Netscape Navigator).
"""

import os
import json
import io
import zipfile
import requests
from flask import request, Response, send_file
from urllib.parse import urlparse, parse_qs

DOMAIN = "github.com"
EXTENSION_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_CACHE_DIR = os.path.join(EXTENSION_DIR, "repo_cache")

os.makedirs(REPO_CACHE_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

GITHUB_API_BASE = "https://api.github.com"


def get_github_users():
	"""Get the list of GitHub users from config."""
	try:
		import config
		return getattr(config, 'GITHUB_USERS', [])
	except (ImportError, AttributeError):
		return []


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
		else:
			print(f"[github] Error fetching repos for {username}: HTTP {resp.status_code}")
			return []
	except Exception as e:
		print(f"[github] Exception fetching repos for {username}: {e}")
		return []


def download_repo_zip(repo_full_name, branch="master"):
	"""Download a repository as a ZIP archive and return the bytes."""
	cache_key = repo_full_name.replace("/", "_") + ".zip"
	cache_path = os.path.join(REPO_CACHE_DIR, cache_key)

	# Check cache first (1 hour validity)
	if os.path.exists(cache_path):
		age = os.path.getmtime(cache_path)
		import time
		if time.time() - age < 3600:
			with open(cache_path, "rb") as f:
				return f.read()

	# Try master branch first, then main
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


def generate_homepage(users_repos):
	"""Generate the main GitHub repos page."""
	html = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">\n<title>GitHub Repo Browser</title>\n</head>\n<body>\n'
	html += '<center><h1>GitHub Repo Browser</h1></center>\n'
	html += '<hr>\n'

	if not users_repos:
		html += '<p>No GitHub users configured. Add GITHUB_USERS to config.py.</p>\n'
		html += '</body>\n</html>'
		return html

	for username, repos in users_repos:
		html += f'<h2>Repositories by <a href="https://github.com/{username}">{username}</a></h2>\n'
		html += '<ul>\n'
		for repo in repos:
			repo_name = repo.get("name", "unknown")
			repo_full = repo.get("full_name", f"{username}/{repo_name}")
			description = repo.get("description") or "No description"
			lang = repo.get("language") or "N/A"
			stars = repo.get("stargazers_count", 0)
			updated = repo.get("updated_at", "")[:10]

			html += '<li>\n'
			html += f'<b><a href="https://github.com/{repo_full}">{repo_name}</a></b><br>\n'
			html += f'<font size="2">{description}<br>\n'
			html += f'Language: {lang} | Stars: {stars} | Updated: {updated}<br>\n'
			html += f'<a href="/download?repo={repo_full}">[Download ZIP]</a>\n'
			html += '</font>\n'
			html += '</li>\n'
		html += '</ul>\n'
		html += '<hr>\n'

	html += '<center><font size="2">GitHub Repo Browser for Macproxy</font></center>\n'
	html += '</body>\n</html>'
	return html


def handle_request(req):
	"""Main request handler for the GitHub extension."""
	parsed_url = urlparse(req.url)
	path = parsed_url.path
	query_params = parse_qs(parsed_url.query)

	# Handle download requests
	if path == "/download" and "repo" in query_params:
		repo_full = query_params["repo"][0]
		zip_data = download_repo_zip(repo_full)
		if zip_data:
			repo_name = repo_full.split("/")[-1]
			return send_file(
				io.BytesIO(zip_data),
				as_attachment=True,
				download_name=f"{repo_name}.zip",
				mimetype="application/zip"
			)
		else:
			return Response("Error: Could not download repository. The repo may be empty or the branch name may differ.", status=500)

	# Fetch repos for all configured users
	users = get_github_users()
	users_repos = []
	for username in users:
		repos = fetch_user_repos(username)
		if repos:
			users_repos.append((username, repos))

	html = generate_homepage(users_repos)
	return html, 200
