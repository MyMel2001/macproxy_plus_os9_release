"""
Project: Golden Years - Time-Machine Web Revival Engine

Fetches archived pages from the Internet Archive Wayback Machine and rewires
their forms and interactive elements to live API backends called "Coffee Extensions".

HOW IT WORKS:
1. Page is fetched from archive.org (like waybackmachine.py)
2. Forms and interactive elements are rewired to POST back to the SAME page URL
3. Hidden fields (_coffee_ext, _coffee_action, _coffee_original_action) route submissions
4. On form submission, the coffee extension returns structured data
5. The ORIGINAL action URL is fetched from the archive
6. The coffee extension's data is applied to that archived page
7. The modified page is served - maintaining authenticity of the original
8. Only payment results show a simulation notice
"""

from flask import request, render_template_string
from openai import OpenAI
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import re
import time
import config
import os
import importlib.util
import json

# Import the coffee extensions system using importlib
_coffee_ext_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'coffee_extensions', '__init__.py')
_coffee_ext_spec = importlib.util.spec_from_file_location("coffee_extensions", _coffee_ext_path)
coffee_extensions_module = importlib.util.module_from_spec(_coffee_ext_spec)
_coffee_ext_spec.loader.exec_module(coffee_extensions_module)

# Re-export for convenience
coffee_extensions_registry = coffee_extensions_module.coffee_extensions
get_extension_descriptions = coffee_extensions_module.get_extension_descriptions
get_extension_creation_prompt = coffee_extensions_module.get_extension_creation_prompt
create_extension_from_ai = coffee_extensions_module.create_extension_from_ai
route_form_action = coffee_extensions_module.route_form_action
action_routes = coffee_extensions_module.action_routes

# Initialize the OpenAI client with admin-specified endpoint
client = OpenAI(
    base_url=config.GOLDEN_YEARS_API_BASE_URL,
    api_key=config.GOLDEN_YEARS_API_KEY
)

DOMAIN = "goldenyears.yay"
TARGET_DATE = "19980710"  # Always July 10th of selected year
last_request_time = 0
REQUEST_DELAY = 0.2

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"

# Create a session object for persistent connections
archive_session = requests.Session()
archive_session.headers.update({'User-Agent': USER_AGENT})

GOLDEN_YEARS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
	<title>Project: Golden Years</title>
</head>
<body>
	<center>{% if not override_active %}<br>{% endif %}
		<font size="7"><h4>Project:<br>Golden Years</h4></font>
		<form method="post">
			{% if override_active %}
				<select name="year">
					{% for y in years %}
						<option value="{{ y }}" {% if y == selected_year %}selected{% endif %}>{{ y }}</option>
					{% endfor %}
				</select>
				<br>
				<input type="submit" name="action" value="set year">
				<input type="submit" name="action" value="disable">
			{% else %}
				<input type="submit" name="action" value="enable">
			{% endif %}
		</form>
		<p>
			{% if override_active %}
				<b>Project: Golden Years enabled!</b><br>
				reviving the web of <b>July {{ selected_year }}</b><br><br>
				enter a URL in the address bar,<br>or click <b>disable</b> to quit.
			{% else %}
				Project: Golden Years disabled.<br>
				Click <b>enable</b> to begin.
			{% endif %}
		</p>
	</center>
</body>
</html>
"""

# Prompt for AI to create a new coffee extension from an unknown form
VIBE_CODE_EXTENSION_PROMPT = """You are "Project: Golden Years", a time-machine web revival engine. You are given the HTML of a form from an archived webpage that needs a live API backend.

Your job is to create a NEW Python coffee extension module that provides a real API backend for this form's functionality.

== THE FORM HTML ==
{{ form_html }}

== YOUR TASK ==
Create a Python module for a new coffee extension that:
1. Has a DOMAIN like "service-name.goldenyears.yay"
2. Has a DESCRIPTION explaining what it does
3. Has ACTION_ROUTES mapping form action patterns to action names
4. Has a handle_action_data(action, params, year) function that returns structured data
5. Uses real APIs where possible (not simulated data)

== RULES ==
- The handle_action_data function receives: action (string), params (dict of form fields), year (int)
- It must return a dict with keys: type, title, content, items (list), is_payment (bool)
- "type" describes the result type (e.g. "search_results", "login_result", "post_result", "data")
- "title" is a short heading for the result
- "content" is the main text/HTML content of the result
- "items" is a list of result items (each a dict with relevant keys)
- "is_payment" should be True only if this involves money/transactions
- Use real public APIs where possible (no API keys needed)
- If no real API exists, return useful information/instructions

== OUTPUT FORMAT ==
Respond with ONLY this JSON structure, nothing else:
{
    "name": "extension_name",
    "description": "What this extension does",
    "domain": "service-name.goldenyears.yay",
    "action_routes": {"form_action_pattern": "action_name"},
    "code": "the complete Python code for the module"
}

The code must include:
- DOMAIN = "service-name.goldenyears.yay"
- DESCRIPTION = "..."
- ACTION_ROUTES = {...}
- def handle_action_data(action, params, year): ... returning a dict
"""

override_active = False
selected_year = 1998
years = list(range(1998, 2013))  # 1998 through 2012 inclusive


def get_override_status():
    global override_active
    return override_active


def rate_limit_request():
    """Implement rate limiting between requests to archive.org"""
    global last_request_time
    current_time = time.time()
    time_since_last_request = current_time - last_request_time
    if time_since_last_request < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - time_since_last_request)
    last_request_time = time.time()


def construct_wayback_url(url, timestamp):
    """Construct a Wayback Machine URL with the given timestamp"""
    return f"https://web.archive.org/web/{timestamp}/{url}"


def find_july_snapshot(url, year):
    """Use Wayback CDX API to find a snapshot, preferring July of the given year.
    
    Searches the entire year, then scores snapshots to prefer July (the default month),
    falling back to June/August, then any other month. Within the same month, picks the
    snapshot closest to July 15th.
    """
    try:
        cdx_url = f"https://web.archive.org/cdx/search/cdx"
        
        # Use a reasonable limit - we only need a few snapshots to find one in July.
        # limit=-1 (unlimited) causes timeouts for popular sites like google.com.
        params = {
            'url': url,
            'matchType': 'prefix',
            'limit': 100,
            'from': f"{year}0101",
            'to': f"{year}1231",
            'output': 'json',
            'filter': '!statuscode:5xx'
        }

        response = archive_session.get(cdx_url, params=params, timeout=225)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 1:
                snapshots = data[1:]

                def score_snapshot(snap):
                    ts = snap[1]
                    month = ts[4:6]
                    if month == "07":
                        return 0
                    elif month == "06":
                        return 1
                    elif month == "08":
                        return 2
                    else:
                        return 3

                snapshots.sort(key=lambda x: (
                    score_snapshot(x),
                    abs(int(x[1][:8]) - int(f"{year}0715"))
                ))

                for snapshot in snapshots:
                    return snapshot[1]

    except Exception as e:
        print(f"Error finding snapshot: {str(e)}")

    return None


def make_archive_request(url, timestamp):
    """Make a request to the archive with rate limiting"""
    rate_limit_request()

    try:
        wayback_url = construct_wayback_url(url, timestamp)
        print(f'[Golden Years] Fetching from archive: {wayback_url}')
        response = archive_session.get(wayback_url, timeout=15)

        if response.status_code == 200:
            content = response.text

            if 'Got an HTTP' in content and 'Redirecting to...' in content:
                redirect_match = re.search(r'Redirecting to\.\.\.\s*\n\s*(.*?)\s*$', content, re.MULTILINE)
                if redirect_match:
                    redirect_url = redirect_match.group(1).strip()
                    print(f'[Golden Years] Following Wayback redirect to: {redirect_url}')
                    return make_archive_request(redirect_url, timestamp)

            if 'window.location.replace' in content:
                redirect_match = re.search(r'window\.location\.replace\(["\'](.+?)["\']\)', content)
                if redirect_match:
                    redirect_url = redirect_match.group(1).strip()
                    print(f'[Golden Years] Following JS redirect to: {redirect_url}')
                    return make_archive_request(redirect_url, timestamp)

        return response

    except Exception as e:
        print(f"[Golden Years] Archive request failed: {str(e)}")
        raise


def strip_wayback_injected_elements(html_content):
    """Remove Wayback Machine's injected toolbar elements and fix URLs"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        for element in soup.select(
            'script[src*="/_static/"], script[src*="archive.org"], '
            'link[href*="/_static/"], div[id*="wm-"], div[class*="wm-"], '
            'style[id*="wm-"], div[id*="donato"], div[id*="playback"], '
            'div[id*="wb-"], div[class*="wb-"]'
        ):
            element.decompose()

        url_attributes = ['href', 'src', 'action']
        for tag in soup.find_all():
            for attr in url_attributes:
                if tag.has_attr(attr):
                    val = tag[attr]
                    match = re.search(r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/(?:https?://)?(.+)', val)
                    if match:
                        original = match.group(1)
                        if not original.startswith(('http://', 'https://')):
                            original = f'http://{original}'
                        tag[attr] = original

        return str(soup)
    except Exception as e:
        print(f"[Golden Years] Error stripping Wayback elements: {str(e)}")
        return html_content


def extract_original_url(url, base_url):
    """Extract original URL from Wayback Machine URL format"""
    try:
        if '_static/' in url:
            return None

        parsed_url = urlparse(url)
        if parsed_url.scheme and parsed_url.netloc and 'web.archive.org' not in parsed_url.netloc:
            return url

        base_match = re.search(r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/(?:https?://)?([^/]+)/?', base_url)
        base_domain = base_match.group(1) if base_match else None

        timestamp_pattern = r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/'
        if re.search(timestamp_pattern, url):
            match = re.search(r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/(?:https?://)?(.+)', url)
            if match:
                actual_url = match.group(1)
                return f'http://{actual_url}' if not actual_url.startswith(('http://', 'https://')) else actual_url

        if not url.startswith(('http://', 'https://')):
            if url.startswith('//'):
                return f'http:{url}'
            elif url.startswith('/'):
                if base_domain:
                    return f'http://{base_domain}{url}'
            else:
                if base_domain:
                    base_path = os.path.dirname(parsed_url.path)
                    if base_path and base_path != '/':
                        return f'http://{base_domain}{base_path}/{url}'
                    else:
                        return f'http://{base_domain}/{url}'

        return url
    except Exception as e:
        print(f"Error in extract_original_url: {url} - {str(e)}")
        return url


def process_html_content(content, base_url):
    """Process HTML content to fix Wayback Machine URLs"""
    try:
        soup = BeautifulSoup(content, 'html.parser')

        # Remove Wayback Machine's injected elements
        for element in soup.select(
            'script[src*="/_static/"], script[src*="archive.org"], '
            'link[href*="/_static/"], div[id*="wm-"], div[class*="wm-"], '
            'style[id*="wm-"], div[id*="donato"], div[id*="playback"]'
        ):
            element.decompose()

        # Process regular URL attributes
        url_attributes = ['href', 'src', 'background', 'data', 'poster', 'action']
        url_pattern = r'url\([\'"]?(\/web\/\d{14}(?:im_|js_|cs_|fw_)?\/(?:https?:\/\/)?[^)]+)[\'"]?\)'

        for tag in soup.find_all():
            for attr in url_attributes:
                if tag.has_attr(attr):
                    original_url = tag[attr]
                    new_url = extract_original_url(original_url, base_url)
                    if new_url:
                        tag[attr] = new_url
                    else:
                        del tag[attr]

            if tag.has_attr('style'):
                style_content = tag['style']
                tag['style'] = re.sub(url_pattern,
                    lambda m: f'url("{extract_original_url(m.group(1), base_url)}")',
                    style_content)

        for style_tag in soup.find_all('style'):
            if style_tag.string:
                style_tag.string = re.sub(url_pattern,
                    lambda m: f'url("{extract_original_url(m.group(1), base_url)}")',
                    style_tag.string)

        return str(soup)
    except Exception as e:
        print(f"Error in process_html_content: {str(e)}")
        return content


def find_matching_action_route(form_action):
    """Find a matching coffee extension action route for a form action URL.
    Returns (domain, action_name) or None if no match."""
    if not form_action:
        return None

    for pattern, (domain, action_name) in action_routes.items():
        if pattern in form_action:
            return (domain, action_name)

    return None


def vibe_code_new_extension(form_html, form_action, year):
    """Use AI to create a new coffee extension for an unknown form.
    Returns the domain name of the created extension, or None on failure."""
    from jinja2 import Template

    prompt_template = Template(VIBE_CODE_EXTENSION_PROMPT)
    system_prompt = prompt_template.render(
        form_html=form_html,
        year=year
    )

    try:
        print(f'[Golden Years] AI vibe-coding new extension for form action: {form_action}')
        response = client.chat.completions.create(
            model=config.GOLDEN_YEARS_MODEL,
            messages=[
                {"role": "system", "content": "You create Python coffee extension modules for archived web forms. Respond ONLY with valid JSON."},
                {"role": "user", "content": system_prompt}
            ],
            max_tokens=4096,
            response_format={"type": "json_object"}
        )

        result_text = response.choices[0].message.content
        result = json.loads(result_text)

        name = result.get("name", "unknown")
        description = result.get("description", "")
        domain = result.get("domain", f"{name}.goldenyears.yay")
        action_routes_dict = result.get("action_routes", {})
        code = result.get("code", "")

        if not code:
            print(f'[Golden Years] AI returned no code for extension {name}')
            return None

        print(f'[Golden Years] Creating new coffee extension: {name} -> {domain}')
        success = create_extension_from_ai(name, code, description, domain, action_routes_dict)

        if success:
            return domain
        return None

    except Exception as e:
        print(f'[Golden Years] Error vibe-coding extension: {str(e)}')
        return None


def apply_data_to_page(page_html, result_data, page_url, year):
    """Apply coffee extension result data to an archived page, then rewire its forms.
    
    Takes the archived page HTML and injects the result data into it,
    maintaining the page's original look and feel. Then rewires the page's
    forms so the chain of interactivity continues.
    """
    soup = BeautifulSoup(page_html, 'html.parser')
    body = soup.find('body')
    if not body:
        return page_html

    result_type = result_data.get('type', 'default')
    title = result_data.get('title', '')
    content = result_data.get('content', '')
    items = result_data.get('items', [])
    is_payment = result_data.get('is_payment', False)

    # Build result HTML that fits the page's style
    result_html = '<hr noshade size="1">\n'

    if title:
        result_html += f'<p><b>{title}</b></p>\n'

    if content:
        result_html += f'<p>{content}</p>\n'

    if items:
        result_html += '<table width="100%">\n'
        for item in items:
            result_html += '<tr><td>'
            if isinstance(item, dict):
                for key, value in item.items():
                    result_html += f'<b>{key}:</b> {value}<br>\n'
            else:
                result_html += f'{item}<br>\n'
            result_html += '</td></tr>\n'
        result_html += '</table>\n'

    # Only payment shows a simulation notice
    if is_payment:
        result_html += '<p><small><b>Payment Simulation:</b> This is a simulated payment. No real transaction has occurred.</small></p>\n'

    result_html += '<hr noshade size="1">\n'

    # Inject at the top of the body
    result_soup = BeautifulSoup(result_html, 'html.parser')
    body.insert(0, result_soup)

    # Rewire the page's forms so the chain continues
    rewire_page_forms(soup, year, page_url)

    return str(soup)


def _rewire_url(url_attr, tag, soup, year, page_url, rewired, created_extensions):
    """Check if a URL attribute on a tag matches a coffee extension and rewire it.
    
    Only handles forms (adds hidden fields) and script src (rewrites with query params).
    Links, images, and other elements are left untouched to preserve page authenticity.
    Returns True if rewired, False otherwise.
    """
    url = tag.get(url_attr, '')
    if not url:
        return False

    # Extract the original URL (strip Wayback prefix)
    original_url = url
    match = re.search(r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/(?:https?://)?(.+)', url)
    if match:
        original_url = match.group(1)
        if not original_url.startswith(('http://', 'https://')):
            original_url = f'http://{original_url}'

    # Check if this matches a known coffee extension
    route_match = find_matching_action_route(original_url)
    if not route_match:
        return False

    domain, action_name = route_match
    tag_name = tag.name.lower()

    if tag_name == 'form':
        # Rewire form: POST to same page URL with hidden fields
        tag['action'] = page_url
        tag['method'] = 'post'
        ext_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_ext', 'value': domain})
        tag.append(ext_hidden)
        act_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_action', 'value': action_name})
        tag.append(act_hidden)
        orig_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_original_action', 'value': original_url})
        tag.append(orig_hidden)
        rewired.append({'type': 'form', 'original': original_url, 'extension': domain, 'action': action_name})
        print(f'[Golden Years] Rewired form: {original_url} -> {domain}/{action_name}')

    elif tag_name == 'script':
        # Rewire script src: rewrite to page URL with query params (API endpoint replacement)
        from urllib.parse import urlencode
        params = {
            '_coffee_ext': domain,
            '_coffee_action': action_name,
            '_coffee_original_action': original_url
        }
        tag[url_attr] = f"{page_url}?{urlencode(params)}"
        rewired.append({'type': 'script', 'original': original_url, 'extension': domain, 'action': action_name})
        print(f'[Golden Years] Rewired script: {original_url} -> {domain}/{action_name}')

    else:
        # Only forms and scripts are rewired; links, images, and other elements stay untouched
        return False

    return True


def _vibe_code_and_rewire(url, tag, soup, year, page_url, created_extensions):
    """Use AI to create a new extension for an unknown URL, then rewire the element.
    
    Only handles forms and script tags. Links, images, and other elements are left untouched.
    """
    tag_name = tag.name.lower()
    if tag_name not in ('form', 'script'):
        return False

    url_attr = 'action' if tag_name == 'form' else 'src'

    # Extract original URL
    original_url = url
    match = re.search(r'/web/\d{14}(?:im_|js_|cs_|fw_|oe_)?/(?:https?://)?(.+)', url)
    if match:
        original_url = match.group(1)
        if not original_url.startswith(('http://', 'https://')):
            original_url = f'http://{original_url}'

    print(f'[Golden Years] No known extension for: {original_url} - vibe-coding new one...')
    element_html = str(tag)
    new_domain = vibe_code_new_extension(element_html, original_url, year)
    if new_domain:
        if tag_name == 'form':
            tag['action'] = page_url
            tag['method'] = 'post'
            ext_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_ext', 'value': new_domain})
            tag.append(ext_hidden)
            act_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_action', 'value': 'default'})
            tag.append(act_hidden)
            orig_hidden = soup.new_tag('input', attrs={'type': 'hidden', 'name': '_coffee_original_action', 'value': original_url})
            tag.append(orig_hidden)
        elif tag_name == 'script':
            from urllib.parse import urlencode
            params = {
                '_coffee_ext': new_domain,
                '_coffee_action': 'default',
                '_coffee_original_action': original_url
            }
            tag[url_attr] = f"{page_url}?{urlencode(params)}"
        created_extensions.append({'original': original_url, 'new_domain': new_domain})
        print(f'[Golden Years] Created new extension for: {original_url} -> {new_domain}')
        return True
    else:
        print(f'[Golden Years] Failed to create extension for: {original_url}')
        return False


def rewire_page_forms(soup, year, page_url):
    """Parse HTML and rewire interactive/API elements to coffee extensions.
    
    Only rewires:
    - Forms (POST to same page URL with hidden fields)
    - Script src (API endpoint URLs rewritten to page URL with query params)
    
    Links, images, and other elements are left untouched to preserve page authenticity.
    
    For each element:
    1. Check if the URL matches a known coffee extension pattern
    2. If yes, rewrite to route through goldenyears
    3. If no, use AI to create a new coffee extension, then rewrite
    
    Returns the modified soup and lists of rewired/created elements.
    """
    rewired = []
    created_extensions = []

    # Process forms and script tags only
    for tag in soup.find_all(['form', 'script']):
        tag_name = tag.name.lower()

        # Forms: check 'action' attribute
        if tag_name == 'form':
            url = tag.get('action', '')
            if url:
                if not _rewire_url('action', tag, soup, year, page_url, rewired, created_extensions):
                    _vibe_code_and_rewire(url, tag, soup, year, page_url, created_extensions)

        # Scripts: check 'src' attribute (API endpoint URLs in script tags)
        elif tag_name == 'script' and tag.has_attr('src'):
            url = tag.get('src', '')
            if url:
                if not _rewire_url('src', tag, soup, year, page_url, rewired, created_extensions):
                    _vibe_code_and_rewire(url, tag, soup, year, page_url, created_extensions)

    return rewired, created_extensions


def is_whitelisted_domain(host):
    """Check if the given host is in the admin's WHITELISTED_DOMAINS config.
    
    Domains on the whitelist are excepted from the Golden Years time-machine
    effect and served live instead of from the Wayback Machine archive.
    """
    if not hasattr(config, 'WHITELISTED_DOMAINS') or not config.WHITELISTED_DOMAINS:
        return False
    return any(host.endswith(whitelisted) for whitelisted in config.WHITELISTED_DOMAINS)


def serve_live_page(req):
    """Fetch a page live (not from archive) for whitelisted domains.
    
    Applies character conversion but does not rewire forms or inject
    coffee extension data, since the page is being served as-is.
    """
    url = req.url
    print(f'[Golden Years] Serving live page (whitelisted): {url}')

    try:
        # Use the same session and headers as the archive fetcher
        live_headers = {'User-Agent': USER_AGENT}
        
        if req.method == 'POST':
            live_response = archive_session.post(url, data=req.form, headers=live_headers, timeout=15)
        else:
            live_response = archive_session.get(url, params=req.args, headers=live_headers, timeout=15)

        content = live_response.content
        if not content:
            raise Exception("Empty response received from live server")

        content_type = live_response.headers.get('Content-Type', '').split(';')[0].strip()
        print(f'[Golden Years] Live page Content-Type: {content_type}')

        # Pass through non-HTML content
        if not content_type.startswith('text/html'):
            return content, live_response.status_code, {'Content-Type': content_type}

        # Decode and apply character conversion
        html_content = content.decode('utf-8', errors='replace')
        should_convert = config.CONVERT_CHARACTERS and config.CONVERSION_TABLE
        if should_convert:
            for key, replacement in config.CONVERSION_TABLE.items():
                if isinstance(replacement, bytes):
                    replacement = replacement.decode("utf-8")
                html_content = html_content.replace(key, replacement)

        return html_content, live_response.status_code, {'Content-Type': 'text/html'}

    except Exception as e:
        print(f"[Golden Years] Error serving live page: {str(e)}")
        return f"<html><body><center><font size=\"7\"><h4>Project:<br>Golden Years</h4></font><p><b>Error serving live page:</b><br>{str(e)}</p><p><a href=\"http://goldenyears.yay/\">back to Golden Years</a></p></center></body></html>", 500, {'Content-Type': 'text/html'}


def handle_request(req):
    global override_active, selected_year

    parsed_url = urlparse(req.url)
    host = parsed_url.netloc.split(':')[0]
    is_goldenyears_domain = host == DOMAIN

    # Handle goldenyears.yay control panel
    if is_goldenyears_domain:
        if req.method == 'POST':
            action = req.form.get('action')
            if action == 'enable':
                override_active = True
            elif action == 'disable':
                override_active = False
            elif action == 'set year':
                override_active = True
                selected_year = int(req.form.get('year'))

        return render_template_string(
            GOLDEN_YEARS_TEMPLATE,
            override_active=override_active,
            years=years,
            selected_year=selected_year
        ), 200

    # Check if the requested domain is whitelisted — serve live instead of archived
    if is_whitelisted_domain(host):
        return serve_live_page(req)

    # For all other domains: fetch from archive.org, rewire forms, serve
    return serve_archived_page(req)


def serve_archived_page(req):
    """Fetch an archived page from Wayback Machine, rewire its forms to coffee extensions, and serve it."""
    global selected_year

    url = req.url
    print(f'[Golden Years] Serving archived page: {url} (July {selected_year})')

    try:
        # Check if this is a form submission (POST or GET with coffee extension hidden fields)
        coffee_ext = None
        coffee_action = 'default'
        original_action = ''
        form_data = {}

        if req.method == 'POST':
            coffee_ext = req.form.get('_coffee_ext')
            coffee_action = req.form.get('_coffee_action', 'default')
            original_action = req.form.get('_coffee_original_action', '')
            for key, value in req.form.items():
                if key not in ('_coffee_ext', '_coffee_action', '_coffee_original_action'):
                    form_data[key] = value
        elif req.method == 'GET':
            # GET forms submit via query string - check for our hidden fields
            coffee_ext = req.args.get('_coffee_ext')
            coffee_action = req.args.get('_coffee_action', 'default')
            original_action = req.args.get('_coffee_original_action', '')
            for key, value in req.args.items():
                if key not in ('_coffee_ext', '_coffee_action', '_coffee_original_action'):
                    form_data[key] = value

        if coffee_ext and coffee_ext in coffee_extensions_registry:
            # This is a form submission to a coffee extension
            module = coffee_extensions_registry[coffee_ext]
            print(f'[Golden Years] Processing coffee extension submission: {coffee_ext}/{coffee_action}')

            # Call the extension's handle_action_data to get structured data
            if hasattr(module, 'handle_action_data'):
                result_data = module.handle_action_data(coffee_action, form_data, selected_year)
            else:
                print(f'[Golden Years] Extension {coffee_ext} has no handle_action_data()')
                result_data = None

            if result_data:
                # Fetch the NEXT page from archive (the form's original action URL)
                next_url = original_action if original_action else url
                print(f'[Golden Years] Fetching next page from archive: {next_url}')

                timestamp = find_july_snapshot(next_url, selected_year)
                if timestamp:
                    archive_response = make_archive_request(next_url, timestamp)
                    if archive_response.status_code == 200:
                        content = archive_response.content
                        if content:
                            html_content = content.decode('utf-8', errors='replace')
                            cleaned_html = strip_wayback_injected_elements(html_content)
                            processed_html = process_html_content(cleaned_html, next_url)

                            # Apply the coffee extension data to the archived page and rewire it
                            final_html = apply_data_to_page(processed_html, result_data, next_url, selected_year)

                            # Apply character conversion
                            should_convert = config.CONVERT_CHARACTERS and config.CONVERSION_TABLE
                            if should_convert:
                                for key, replacement in config.CONVERSION_TABLE.items():
                                    if isinstance(replacement, bytes):
                                        replacement = replacement.decode("utf-8")
                                    final_html = final_html.replace(key, replacement)

                            return final_html, 200, {'Content-Type': 'text/html'}

            # If something went wrong, just serve the page normally
            print(f'[Golden Years] Coffee extension returned no data, serving page normally')

            if coffee_ext and coffee_ext in coffee_extensions_registry:
                # This is a form submission to a coffee extension
                module = coffee_extensions_registry[coffee_ext]
                print(f'[Golden Years] Processing coffee extension submission: {coffee_ext}/{coffee_action}')

                # Collect form data (excluding our hidden fields)
                form_data = {}
                for key, value in req.form.items():
                    if key not in ('_coffee_ext', '_coffee_action', '_coffee_original_action'):
                        form_data[key] = value

                # Call the extension's handle_action_data to get structured data
                if hasattr(module, 'handle_action_data'):
                    result_data = module.handle_action_data(coffee_action, form_data, selected_year)
                else:
                    print(f'[Golden Years] Extension {coffee_ext} has no handle_action_data()')
                    result_data = None

                if result_data:
                    # Fetch the NEXT page from archive (the form's original action URL)
                    next_url = original_action if original_action else url
                    print(f'[Golden Years] Fetching next page from archive: {next_url}')

                    timestamp = find_july_snapshot(next_url, selected_year)
                    if timestamp:
                        archive_response = make_archive_request(next_url, timestamp)
                        if archive_response.status_code == 200:
                            content = archive_response.content
                            if content:
                                html_content = content.decode('utf-8', errors='replace')
                                cleaned_html = strip_wayback_injected_elements(html_content)
                                processed_html = process_html_content(cleaned_html, next_url)

                                # Apply the coffee extension data to the archived page
                                final_html = apply_data_to_page(processed_html, result_data, next_url)

                                # Apply character conversion
                                should_convert = config.CONVERT_CHARACTERS and config.CONVERSION_TABLE
                                if should_convert:
                                    for key, replacement in config.CONVERSION_TABLE.items():
                                        if isinstance(replacement, bytes):
                                            replacement = replacement.decode("utf-8")
                                        final_html = final_html.replace(key, replacement)

                                return final_html, 200, {'Content-Type': 'text/html'}

                # If something went wrong, just serve the page normally
                print(f'[Golden Years] Coffee extension returned no data, serving page normally')

        # Normal GET request or fallback: fetch and serve the archived page
        timestamp = find_july_snapshot(url, selected_year)

        if not timestamp:
            return f"<html><body><center><font size=\"7\"><h4>Project:<br>Golden Years</h4></font><p><b>No archived snapshot found</b><br>for {url} in July {selected_year}.</p><p><a href=\"http://goldenyears.yay/\">back to Golden Years</a></p></center></body></html>", 404, {'Content-Type': 'text/html'}

        archive_response = make_archive_request(url, timestamp)
        content = archive_response.content
        if not content:
            raise Exception("Empty response received from archive")

        content_type = archive_response.headers.get('Content-Type', '').split(';')[0].strip()
        print(f'[Golden Years] Archive Content-Type: {content_type}')

        # Pass through non-HTML content
        if not content_type.startswith('text/html'):
            return content, archive_response.status_code, {'Content-Type': content_type}

        # Decode and process the HTML
        html_content = content.decode('utf-8', errors='replace')
        cleaned_html = strip_wayback_injected_elements(html_content)
        processed_html = process_html_content(cleaned_html, url)

        # Parse with BeautifulSoup and rewire forms to coffee extensions
        soup = BeautifulSoup(processed_html, 'html.parser')
        rewired, created = rewire_page_forms(soup, selected_year, url)

        if rewired or created:
            print(f'[Golden Years] Rewired {len(rewired)} form(s), created {len(created)} new extension(s)')
            final_html = str(soup)
        else:
            final_html = processed_html

        # Apply character conversion if configured
        should_convert = config.CONVERT_CHARACTERS and config.CONVERSION_TABLE
        if should_convert:
            for key, replacement in config.CONVERSION_TABLE.items():
                if isinstance(replacement, bytes):
                    replacement = replacement.decode("utf-8")
                final_html = final_html.replace(key, replacement)

        return final_html, archive_response.status_code, {'Content-Type': 'text/html'}

    except Exception as e:
        print(f"[Golden Years] Error: {str(e)}")
        return f"<html><body><center><font size=\"7\"><h4>Project:<br>Golden Years</h4></font><p><b>Error serving page:</b><br>{str(e)}</p><p><a href=\"http://goldenyears.yay/\">back to Golden Years</a></p></center></body></html>", 500, {'Content-Type': 'text/html'}
