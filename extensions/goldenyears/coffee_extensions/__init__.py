"""
Coffee Extensions - Real API backends for Project: Golden Years revived pages.

Each coffee extension is a Python module in this directory that provides
real API functionality mapped to period-authentic web services.

When Golden Years fetches an archived page from the Internet Archive, it
parses the page's forms and rewires them to POST back to the same page URL
with hidden fields (_coffee_ext, _coffee_action, _coffee_original_action)
that tell the system which extension to call and where to fetch the next page.

On form submission:
1. Coffee extension's handle_action_data() returns structured data
2. The form's original action URL is fetched from the archive
3. The extension's data is applied to that archived page
4. The modified page is served - maintaining the original page's look/feel
5. Only payment results show a simulation notice

Standard interface for each coffee extension:
    DOMAIN = "service-name.goldenyears.yay"  # The domain this extension handles
    DESCRIPTION = "What this extension does"     # Description for the AI prompt
    ACTION_ROUTES = {"pattern": "action_name"}  # Form action pattern -> action mapping
    
    def handle_action_data(action, params, year):
        '''Handle a form action and return structured data.
        - action: string (the action name from ACTION_ROUTES)
        - params: dict of form fields / query parameters
        - year: int (the selected year for era context)
        Returns: dict with keys:
            type (str): result type (e.g. "search_results", "login_result", "payment")
            title (str): short heading for the result
            content (str): main text/HTML content
            items (list): list of result items (each a dict)
            is_payment (bool): True if this involves money/transactions
        '''
        ...

The loader discovers all modules in this directory automatically.
"""

import os
import importlib
import importlib.util

COFFEE_EXTENSIONS_DIR = os.path.dirname(os.path.abspath(__file__))

# Registry of loaded coffee extensions
# Maps domain -> module reference
coffee_extensions = {}

# Maps action pattern -> (domain, action_name)
# e.g. "twitter.com/login" -> ("bluesky.goldenyears.yay", "login")
action_routes = {}


def discover_coffee_extensions():
    """Scan the coffee-extensions directory and load all coffee extension modules."""
    global coffee_extensions, action_routes

    coffee_extensions = {}
    action_routes = {}

    for filename in os.listdir(COFFEE_EXTENSIONS_DIR):
        if not filename.endswith('.py'):
            continue
        if filename == '__init__.py':
            continue

        module_name = filename[:-3]  # Strip .py
        filepath = os.path.join(COFFEE_EXTENSIONS_DIR, filename)

        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, 'DOMAIN') and (hasattr(module, 'handle_action_data') or hasattr(module, 'handle_action')):
                coffee_extensions[module.DOMAIN] = module
                print(f'[Coffee Extensions] Loaded: {module_name} -> {module.DOMAIN}')

                # Register action routes if the module provides them
                if hasattr(module, 'ACTION_ROUTES'):
                    for pattern, action_name in module.ACTION_ROUTES.items():
                        action_routes[pattern] = (module.DOMAIN, action_name)

        except Exception as e:
            print(f'[Coffee Extensions] Error loading {module_name}: {e}')

    print(f'[Coffee Extensions] Loaded {len(coffee_extensions)} extension(s)')
    return coffee_extensions


def route_form_action(form_action, form_data, year):
    """
    Route a form submission from a revived page to the appropriate coffee extension.
    
    Returns structured data dict if routed, None if no route matches.
    """
    if not form_action:
        return None

    # Check direct action routes first
    for pattern, (domain, action_name) in action_routes.items():
        if pattern in form_action:
            module = coffee_extensions.get(domain)
            if module:
                print(f'[Coffee Extensions] Routing {form_action} -> {domain}/{action_name}')
                if hasattr(module, 'handle_action_data'):
                    return module.handle_action_data(action_name, form_data, year)
                return module.handle_action(action_name, form_data, year)

    # Check by domain match
    for domain, module in coffee_extensions.items():
        if domain in form_action:
            print(f'[Coffee Extensions] Routing {form_action} -> {domain}')
            if hasattr(module, 'handle_action_data'):
                return module.handle_action_data('default', form_data, year)
            return module.handle_action('default', form_data, year)

    return None


def get_extension_descriptions():
    """Return a formatted string describing all available coffee extensions for the AI prompt."""
    descriptions = []
    for domain, module in coffee_extensions.items():
        if hasattr(module, 'DESCRIPTION'):
            descriptions.append(f"  - {domain}: {module.DESCRIPTION}")
        if hasattr(module, 'ACTION_ROUTES') and module.ACTION_ROUTES:
            routes = ", ".join(module.ACTION_ROUTES.keys())
            descriptions.append(f"    Routes: {routes}")
    return "\n".join(descriptions)


def get_extension_creation_prompt():
    """Return instructions for the AI on how to create new coffee extensions."""
    return """
== COFFEE EXTENSION CREATION ==
If you encounter a website with functionality that needs a real API backend
(like a social media feed, payment system, search engine, etc.), you can create
a new coffee extension by outputting a special code block at the END of your HTML response.

To create a new extension, append this block AFTER your closing </html> tag:

<!--COFFEE_EXTENSION:extension_name
DESCRIPTION="What this extension does"
DOMAIN="service-name.goldenyears.yay"
ACTION_ROUTES={"pattern1": "action1", "pattern2": "action2"}
CODE=
import requests
DOMAIN = "service-name.goldenyears.yay"
DESCRIPTION = "What this extension does"
ACTION_ROUTES = {"pattern1": "action1", "pattern2": "action2"}

def handle_action_data(action, params, year):
    if action == "action1":
        # Real API call here
        return {"type": "data", "title": "Result", "content": "...", "items": [], "is_payment": False}
    return {"type": "error", "title": "Error", "content": "Not implemented", "items": [], "is_payment": False}
-->

The system will automatically extract this and create the extension file.
Use this for:
- Social media feeds (map Twitter -> Bluesky, Facebook -> Mastodon)
- Payment systems (map PayPal -> cryptocurrency via CoinGecko API)
- Search engines (map Google -> real search)
- Video platforms (map YouTube -> Invidious)
- News feeds (map RSS -> real RSS feeds)
- Any other service that needs a live API backend
"""


def create_extension_from_ai(name, code, description, domain, action_routes_dict):
    """Create a new coffee extension file from AI-generated code."""
    filepath = os.path.join(COFFEE_EXTENSIONS_DIR, f"coffee_{name}.py")

    # Build the module content
    module_content = f'''"""
Coffee Extension: {name}
{description}
Auto-generated by Project: Golden Years AI
"""

import requests
import json

DOMAIN = "{domain}"
DESCRIPTION = """{description}"""

ACTION_ROUTES = {repr(action_routes_dict) if action_routes_dict else "{{}}"}

{code}
'''

    try:
        with open(filepath, 'w') as f:
            f.write(module_content)
        print(f'[Coffee Extensions] Created new extension: coffee_{name}.py')

        # Reload to make it available immediately
        discover_coffee_extensions()
        return True
    except Exception as e:
        print(f'[Coffee Extensions] Error creating extension {name}: {e}')
        return False


# Auto-discover on import
discover_coffee_extensions()
