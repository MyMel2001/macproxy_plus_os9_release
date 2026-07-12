from flask import request, make_response, redirect
import config
import json
import urllib.parse

DOMAIN = "settings.config"

COOKIE_NAME = "macproxy_settings"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year

# Default settings keys
SETTING_KEYS = [
    "BLUESKY_HANDLE",
    "BLUESKY_APP_PASSWORD",
    "BLUESKY_PDS_URL",
    "ZIP_CODE",
    "GITHUB_USERS",
]


def _serialize_github_users(user_list):
    """Convert a list of GitHub usernames to a comma-separated string."""
    if isinstance(user_list, list):
        return ", ".join(user_list)
    return str(user_list) if user_list else ""


def _deserialize_github_users(user_string):
    """Convert a comma-separated string of GitHub usernames to a list."""
    if isinstance(user_string, list):
        return user_string
    if not user_string or not user_string.strip():
        return []
    return [u.strip() for u in user_string.split(",") if u.strip()]


def get_settings_from_cookie():
    """Read settings from the request cookie."""
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    if not cookie_value:
        return {}

    try:
        decoded = urllib.parse.unquote(cookie_value)
        return json.loads(decoded)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[PublicMode] Error decoding cookie: {e}")
        return {}


def apply_settings_to_config(settings):
    """Patch config module with the given settings dict."""
    if settings.get("BLUESKY_HANDLE"):
        config.BLUESKY_HANDLE = settings["BLUESKY_HANDLE"]
    if settings.get("BLUESKY_APP_PASSWORD"):
        config.BLUESKY_APP_PASSWORD = settings["BLUESKY_APP_PASSWORD"]
    if settings.get("BLUESKY_PDS_URL"):
        config.BLUESKY_PDS_URL = settings["BLUESKY_PDS_URL"]
    if settings.get("ZIP_CODE"):
        config.ZIP_CODE = settings["ZIP_CODE"]
    if settings.get("GITHUB_USERS"):
        config.GITHUB_USERS = _deserialize_github_users(settings["GITHUB_USERS"])


def apply_cookie_settings():
    """Called before each request to apply per-user cookie settings to config."""
    settings = get_settings_from_cookie()
    if settings:
        apply_settings_to_config(settings)


def build_cookie_value(settings):
    """Build a URL-encoded JSON cookie value from a settings dict."""
    # Only store non-empty values
    to_store = {k: v for k, v in settings.items() if v}
    return urllib.parse.quote(json.dumps(to_store))


SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
	<title>Public Mode Settings</title>
</head>
<body>
	<center>
		<h1><font size="7"><h4>Public Mode<br>Settings</h4></font></h1>
		<p>Configure your personal preferences below.<br>
		These settings are stored in a cookie on your browser<br>
		and are used by extensions like Weather, GitHub,<br>
		and Golden Years.</p>
	</center>
	<hr>
	<form method="post">
		<p><strong>Bluesky Handle:</strong><br>
		<input type="text" name="bluesky_handle" value="{{ bluesky_handle }}" size="40"><br>
		<small>Your Bluesky handle<br>
		(e.g. your-handle.bsky.social)</small></p>
		<p><strong>Bluesky App Password:</strong><br>
		<input type="password" name="bluesky_app_password" value="{{ bluesky_app_password }}" size="40"><br>
		<small>Your Bluesky app password<br>
		(not your main account password)</small></p>
		<p><strong>Bluesky PDS URL:</strong><br>
		<input type="text" name="pds_url" value="{{ pds_url }}" size="40"><br>
		<small>Your Personal Data Server endpoint<br>
		(e.g. https://bsky.social)</small></p>
		<p><strong>Zip Code:</strong><br>
		<input type="text" name="zip_code" value="{{ zip_code }}" size="10"><br>
		<small>Your US zip code for weather forecasts</small></p>
		<p><strong>GitHub Users:</strong><br>
		<input type="text" name="github_users" value="{{ github_users }}" size="50"><br>
		<small>Comma-separated GitHub usernames<br>
		whose repos will appear on github.com</small></p>
		<hr>
		<center>
			<input type="submit" name="action" value="Save Settings">
			&nbsp;&nbsp;
			<input type="submit" name="action" value="Clear Settings">
		</center>
	</form>
	{% if message %}
	<center><p><strong>{{ message }}</strong></p></center>
	{% endif %}
	<hr>
	<center>
		<p><small>Public Mode &mdash; personal settings for Macproxy</small></p>
	</center>
</body>
</html>
"""


def handle_request(req):
    settings = get_settings_from_cookie()

    message = ""

    if req.method == 'POST':
        action = req.form.get('action')

        if action == 'Save Settings':
            new_settings = {
                "BLUESKY_HANDLE": req.form.get('bluesky_handle', '').strip(),
                "BLUESKY_APP_PASSWORD": req.form.get('bluesky_app_password', '').strip(),
                "BLUESKY_PDS_URL": req.form.get('pds_url', '').strip(),
                "ZIP_CODE": req.form.get('zip_code', '').strip(),
                "GITHUB_USERS": req.form.get('github_users', '').strip(),
            }
            cookie_value = build_cookie_value(new_settings)
            resp = make_response(redirect("http://settings.config/"))
            resp.set_cookie(
                COOKIE_NAME,
                value=cookie_value,
                max_age=COOKIE_MAX_AGE,
                path="/",
            )
            return resp

        elif action == 'Clear Settings':
            resp = make_response(redirect("http://settings.config/"))
            resp.set_cookie(COOKIE_NAME, value="", max_age=0, path="/")
            return resp

    # Pre-fill from cookie, falling back to config.py defaults
    pds_url = settings.get("BLUESKY_PDS_URL", getattr(config, 'BLUESKY_PDS_URL', ''))
    bluesky_handle = settings.get("BLUESKY_HANDLE", getattr(config, 'BLUESKY_HANDLE', ''))
    bluesky_app_password = settings.get("BLUESKY_APP_PASSWORD", getattr(config, 'BLUESKY_APP_PASSWORD', ''))
    zip_code = settings.get("ZIP_CODE", str(getattr(config, 'ZIP_CODE', '')))
    github_users = settings.get("GITHUB_USERS", _serialize_github_users(getattr(config, 'GITHUB_USERS', [])))

    from flask import render_template_string
    return render_template_string(
        SETTINGS_TEMPLATE,
        pds_url=pds_url,
        bluesky_handle=bluesky_handle,
        bluesky_app_password=bluesky_app_password,
        zip_code=zip_code,
        github_users=github_users,
        message=message
    )
