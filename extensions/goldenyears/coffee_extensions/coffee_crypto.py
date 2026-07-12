"""
Coffee Extension: Crypto Payments
Maps PayPal, credit card, and payment functionality to cryptocurrency price lookups
and simulated crypto transactions via public APIs.

handle_action_data() returns structured data that goldenyears applies to the
archived page fetched from the form's original action URL.
Payment results include is_payment=True so goldenyears shows a simulation notice.
"""

import requests
import json
import urllib.parse

DOMAIN = "crypto.goldenyears.yay"
DESCRIPTION = "Payment/crypto backend. Maps PayPal/Visa/Mastercard/checkout flows to cryptocurrency info and price lookups via CoinGecko API."

ACTION_ROUTES = {
    "paypal.com": "pay",
    "paypal.com/cgi-bin/webscr": "pay",
    "checkout": "pay",
    "cart": "cart",
    "shop": "shop",
    "buy": "pay",
    "donate": "donate",
    "payment": "pay",
    "subscribe": "subscribe",
}


def _get_crypto_price(coin="bitcoin", currency="usd"):
    """Fetch current crypto price from CoinGecko."""
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coin,
            "vs_currencies": currency
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get(coin, {}).get(currency)
    except Exception as e:
        print(f"[Coffee:Crypto] Price error: {e}")
    return None


def _get_top_crypto(limit=10):
    """Fetch top cryptocurrencies by market cap."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": limit, "page": 1},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Coffee:Crypto] Top coins error: {e}")
    return None


def handle_action_data(action, params, year):
    """Handle a payment/crypto action and return structured data for applying to the archived page.
    Returns a dict with keys: type, title, content, items, is_payment
    Payment results set is_payment=True so goldenyears shows a simulation notice."""

    if action == "pay":
        amount = params.get("amount") or params.get("price") or params.get("value") or ""
        item = params.get("item_name") or params.get("product") or params.get("description") or "item"

        btc_price = _get_crypto_price("bitcoin", "usd")
        eth_price = _get_crypto_price("ethereum", "usd")

        if amount and btc_price:
            try:
                usd_amount = float(amount)
                btc_amount = usd_amount / btc_price
                eth_amount = usd_amount / eth_price if eth_price else 0

                items = [
                    {"Currency": "Bitcoin (BTC)", "Amount": f"{btc_amount:.8f}", "Address": "bc1qgoldenyears"},
                    {"Currency": "Ethereum (ETH)", "Amount": f"{eth_amount:.6f}", "Address": "0xGoldenYears"}
                ]

                return {
                    "type": "payment",
                    "title": "Crypto Checkout",
                    "content": f"Item: {item} | Amount: ${usd_amount:.2f} USD",
                    "items": items,
                    "is_payment": True  # This triggers the simulation notice
                }
            except ValueError:
                pass

        # Show price dashboard if no amount
        return _price_dashboard_data(btc_price, eth_price)

    elif action == "donate":
        btc_price = _get_crypto_price("bitcoin", "usd")
        items = [
            {"Currency": "Bitcoin (BTC)", "Address": "bc1qgoldenyears"},
            {"Currency": "Ethereum (ETH)", "Address": "0xGoldenYears"},
            {"Currency": "Litecoin (LTC)", "Address": "Lgoldenyears"}
        ]
        return {
            "type": "donate",
            "title": "Donate with Crypto",
            "content": f"BTC: ${btc_price:,.2f} USD" if btc_price else "Support this site with cryptocurrency!",
            "items": items,
            "is_payment": True  # Donations involve money
        }

    elif action == "cart":
        return {
            "type": "cart",
            "title": "Shopping Cart",
            "content": "Your cart is empty.",
            "items": [],
            "is_payment": False
        }

    elif action == "shop":
        btc_price = _get_crypto_price("bitcoin", "usd")
        items = []
        if btc_price:
            items = [
                {"Item": "Digital Art Pack", "Price (USD)": "$25.00", "Price (BTC)": f"{25/btc_price:.8f}"},
                {"Item": "Retro Software Bundle", "Price (USD)": "$50.00", "Price (BTC)": f"{50/btc_price:.8f}"},
                {"Item": "Golden Years T-Shirt", "Price (USD)": "$35.00", "Price (BTC)": f"{35/btc_price:.8f}"},
            ]
        return {
            "type": "shop",
            "title": "Crypto Emporium",
            "content": f"BTC: ${btc_price:,.2f}" if btc_price else "Browse our items",
            "items": items,
            "is_payment": False
        }

    elif action == "subscribe":
        return {
            "type": "subscribe",
            "title": "Subscribe with Crypto",
            "content": "Choose your plan:",
            "items": [
                {"Plan": "Basic", "Price": "$5/mo"},
                {"Plan": "Premium", "Price": "$15/mo"},
                {"Plan": "Lifetime", "Price": "$150"},
            ],
            "is_payment": True  # Subscriptions involve money
        }

    return _price_dashboard_data()


def _price_dashboard_data(btc_price=None, eth_price=None):
    """Return structured data for the crypto price dashboard."""
    if not btc_price:
        btc_price = _get_crypto_price("bitcoin", "usd")
    if not eth_price:
        eth_price = _get_crypto_price("ethereum", "usd")

    top_coins = _get_top_crypto(5)
    items = []
    if top_coins:
        for coin in top_coins:
            name = coin.get("name", "")
            symbol = coin.get("symbol", "").upper()
            price = coin.get("current_price", 0)
            change = coin.get("price_change_percentage_24h", 0)
            arrow = "+" if change >= 0 else ""
            items.append({
                "Asset": f"{name} ({symbol})",
                "Price (USD)": f"${price:,.2f}",
                "24h Change": f"{arrow}{change:.1f}%"
            })

    return {
        "type": "price_dashboard",
        "title": "Crypto Dashboard",
        "content": f"BTC: ${btc_price:,.2f} | ETH: ${eth_price:,.2f}" if btc_price else "Real prices via CoinGecko API",
        "items": items,
        "is_payment": False
    }


def handle_action(action, params, year):
    """Legacy handler - kept for compatibility. Returns structured data via handle_action_data."""
    return handle_action_data(action, params, year)
