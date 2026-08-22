import datetime
import json
import logging
import sys
import time
import uuid
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path

__version__ = "0.2.0"

_PYTHON_CMD = Path(sys.executable).stem

import requests

from .exception import AlexaSessionExpiredException, DuplicateListNameException, DefaultListModificationException
from .resource import List, ListItem

logger = logging.getLogger(__name__)

# Maps amazon_domain → cookie suffix used in ubid-*, at-*, sess-at-* cookie names.
# Used as a startup fallback only — the actual suffix is detected from real cookie names
# at runtime and overrides this value on first load or token exchange.
_DOMAIN_COOKIE_SUFFIX: dict[str, str] = {
    "amazon.com":    "main",
    "amazon.co.uk":  "acbuk",
    "amazon.de":     "acbde",
    "amazon.fr":     "acbfr",
    "amazon.it":     "acbit",
    "amazon.es":     "acbes",
    "amazon.co.jp":  "acbjp",
    "amazon.ca":     "acbca",
    "amazon.com.au": "acbau",
    "amazon.in":     "acbin",
    "amazon.com.br": "acbbr",
    "amazon.com.mx": "acbmx",
}

# Maps amazon_domain → Amazon marketplace ID used in API aggregation fields.
_DOMAIN_MARKETPLACE_ID: dict[str, str] = {
    "amazon.com":    "ATVPDKIKX0DER",
    "amazon.co.uk":  "A1F83G8C2ARO7P",
    "amazon.de":     "A1PA6795UKMFR9",
    "amazon.fr":     "A13V1IB3VIYZZH",
    "amazon.it":     "APJ6JRA9NG5V4",
    "amazon.es":     "A1RKKUPIHCS9HS",
    "amazon.co.jp":  "A1VC38T7YXB528",
    "amazon.ca":     "A2EUQ1WTGCTBG2",
    "amazon.com.au": "A39IBJ37TRP1C6",
    "amazon.in":     "A21TJRUUN4KGV",
    "amazon.com.br": "A2Q3Y263D00KWC",
    "amazon.com.mx": "A1AM78C64UM0Y8",
}

# Maps amazon_domain → locale tag used in keyword-recommendations and lc-main cookie.
_DOMAIN_LOCALE: dict[str, str] = {
    "amazon.com":    "en_US",
    "amazon.co.uk":  "en_GB",
    "amazon.de":     "de_DE",
    "amazon.fr":     "fr_FR",
    "amazon.it":     "it_IT",
    "amazon.es":     "es_ES",
    "amazon.co.jp":  "ja_JP",
    "amazon.ca":     "en_CA",
    "amazon.com.au": "en_AU",
    "amazon.in":     "en_IN",
    "amazon.com.br": "pt_BR",
    "amazon.com.mx": "es_MX",
}

class _AmazonSession(requests.Session):
    """requests.Session subclass that injects a unique x-amzn-RequestId into every request."""

    def request(self, method, url, **kwargs) -> requests.Response:
        kwargs.setdefault("headers", {})["x-amzn-RequestId"] = str(uuid.uuid4())
        return super().request(method, url, **kwargs)

class AlexaAPI:
    """Low-level HTTP client for Amazon's private Alexa shopping lists API."""

    def __init__(self, cookie_expiry_retries: int = 0, retry_interval_seconds: int = 30, amazon_domain: str = "amazon.com", service_auth_path: "Path | str | None" = None, runtime_creds_path: "Path | str | None" = None) -> None:
        if amazon_domain not in _DOMAIN_COOKIE_SUFFIX:
            raise ValueError(f"Unsupported amazon_domain '{amazon_domain}'. Supported: {sorted(_DOMAIN_COOKIE_SUFFIX)}")
        self._amazon_domain = amazon_domain
        self._www_domain = f"www.{amazon_domain}"
        self._cookie_suffix = _DOMAIN_COOKIE_SUFFIX[amazon_domain]
        self._marketplace_id = _DOMAIN_MARKETPLACE_ID[amazon_domain]
        self._locale = _DOMAIN_LOCALE[amazon_domain]
        self._session = _AmazonSession()
        self._session_expiry: datetime.datetime | None = None
        self._cookie_expiry_retries = cookie_expiry_retries
        self._retry_interval_seconds = retry_interval_seconds
        self._last_expiry_warning: datetime.datetime | None = None
        self._service_auth_path: Path | None = Path(service_auth_path) if service_auth_path else None
        # Derive runtime_creds_path from service_auth_path (same config directory) when not set explicitly
        if runtime_creds_path is not None:
            self._runtime_creds_path: Path | None = Path(runtime_creds_path)
        elif service_auth_path is not None:
            self._runtime_creds_path = Path(service_auth_path).parent / "runtime_credentials.json"
        else:
            self._runtime_creds_path = None  # resolved at call time to Path.cwd() / "config" / ...
        self.updateHeader()
        self._load_session()
        self._endpoint_list_api = f"https://{self._www_domain}/alexashoppinglists/api/v2/lists/"

    @staticmethod
    def _detect_cookie_suffix(cookies: dict) -> "str | None":
        """Read the regional cookie suffix from actual cookie names.

        Looks for a 'ubid-<suffix>' cookie (e.g. 'ubid-main', 'ubid-acbuk') and
        returns the suffix part. Returns None if no ubid cookie is found.
        """
        ubid = next((name for name in cookies if name.startswith("ubid-")), None)
        return ubid.split("-", 1)[1] if ubid else None

    @staticmethod
    def _safe_json(response: requests.Response, label: str = "") -> "dict | None":
        """Decode the response JSON, returning None and logging a warning on empty or invalid bodies.

        Args:
            response: The requests.Response to parse.
            label: Optional context string included in warning messages.
        Returns:
            Parsed dict, or None if the body is empty or unparseable.
        """
        if not response.text:
            logger.warning("Empty response body from Alexa API%s (status %d)", f" [{label}]" if label else "", response.status_code)
            return None
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as e:
            logger.warning("Invalid JSON from Alexa API%s (status %d): %s", f" [{label}]" if label else "", response.status_code, e)
            return None

    @staticmethod
    def load_credentials(runtime_creds_path: "Path | str | None" = None) -> "tuple[dict, datetime.datetime | None, str | None]":
        if runtime_creds_path is None:
            runtime_creds_path = Path.cwd() / "config" / "runtime_credentials.json"
        with open(runtime_creds_path, "r") as f:
            cookies = json.load(f)["alexa"]["cookies"]

        expiry_map: dict[str, datetime.datetime] = {}
        for name, cookie in cookies.items():
            raw = cookie.get("Expires")
            expiry_map[name] = parsedate_to_datetime(raw)

        earliest_name = min(expiry_map, key=expiry_map.get)
        earliest = expiry_map[earliest_name]

        return cookies, earliest, earliest_name

    def _load_session(self) -> None:
        """Load credentials from the cookie cache, bootstrapping via token exchange if needed."""
        runtime_creds_path = self._runtime_creds_path
        if runtime_creds_path is None:
            runtime_creds_path = Path.cwd() / "config" / "runtime_credentials.json"

        if not Path(runtime_creds_path).exists():
            logger.info("runtime_credentials.json not found — bootstrapping via token exchange")
            if not self._refresh_session():
                raise FileNotFoundError(
                    f"Alexa credentials not found at {runtime_creds_path} and auto-refresh failed. "
                    f"Ensure service_auth.json has a valid alexa.refresh_token."
                )
            return  # _refresh_session() already loaded and applied the session

        try:
            cookies, expiry, expiry_cookie = self.load_credentials(self._runtime_creds_path)
            detected = self._detect_cookie_suffix(cookies)
            if detected:
                self._cookie_suffix = detected
            self._session_expiry = expiry
            self._expiry_cookie: str = expiry_cookie
            self._raw_cookies: dict = cookies
            self._apply_cookies(cookies)
        except KeyError as e:
            # Cached cookies don't have the expected regional cookie names — most likely
            # amazon_domain was changed while an old-region runtime_credentials.json exists.
            logger.info("Cached cookies missing expected cookie %s — refreshing via token exchange", e)
            if not self._refresh_session():
                raise
            return
        self.check_session_expiry()

    def _refresh_session(self) -> bool:
        """Exchange the stored refresh token for new session cookies and apply them.

        Reads `alexa.refresh_token` from service_auth.json, calls the Amazon token
        exchange endpoint, writes new cookies to runtime_credentials.json, and reloads
        the session without triggering another expiry check.

        Returns:
            True if the token exchange succeeded and credentials were reloaded.
            False on any failure (missing file, bad token, network error, etc.).
        """
        service_auth_path = self._service_auth_path
        if service_auth_path is None:
            service_auth_path = Path.cwd() / "config" / "service_auth.json"

        try:
            with open(service_auth_path, "r") as f:
                refresh_token = json.load(f)["alexa"]["refresh_token"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
            logger.debug("Auto-refresh skipped — could not read refresh token: %s", e)
            return False

        if not refresh_token:
            logger.debug("Auto-refresh skipped — refresh token is empty")
            return False

        try:
            response = requests.post(
                "https://api.amazon.com/ap/exchangetoken/cookies",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "x-amzn-identity-auth-domain": "api.amazon.com",
                },
                data={
                    "requested_token_type": "auth_cookies",
                    "app_name": "Amazon Alexa",
                    "domain": self._www_domain,
                    "source_token_type": "refresh_token",
                    "source_token": refresh_token,
                },
                timeout=30,
            )
            response.raise_for_status()
            raw_cookies = response.json()["response"]["tokens"]["cookies"][f".{self._amazon_domain}"]
        except Exception as e:
            logger.warning("Auto-refresh failed — token exchange error: %s", e)
            return False

        cookies = {c["Name"]: c for c in raw_cookies}
        detected = self._detect_cookie_suffix(cookies)
        if detected:
            self._cookie_suffix = detected

        runtime_creds_path = self._runtime_creds_path
        if runtime_creds_path is None:
            runtime_creds_path = Path.cwd() / "config" / "runtime_credentials.json"

        try:
            runtime = {}
            if Path(runtime_creds_path).exists():
                with open(runtime_creds_path, "r") as f:
                    runtime = json.load(f)
            runtime.setdefault("alexa", {})["cookies"] = cookies
            with open(runtime_creds_path, "w") as f:
                json.dump(runtime, f, indent=4)
        except Exception as e:
            logger.warning("Auto-refresh failed — could not write updated credentials: %s", e)
            return False

        try:
            cookies, expiry, expiry_cookie = self.load_credentials(runtime_creds_path)
            self._session_expiry = expiry
            self._expiry_cookie = expiry_cookie
            self._raw_cookies = cookies
            self._apply_cookies(cookies)
        except Exception as e:
            logger.warning("Auto-refresh failed — could not reload credentials: %s", e)
            return False

        return True

    def check_session_expiry(self) -> None:
        """Check whether the session is expired or approaching expiry.

        On expiry, first attempts automatic token exchange via _refresh_session().
        If auto-refresh fails and cookie_expiry_retries > 0, falls back to the
        manual retry loop — waiting retry_interval_seconds between attempts and
        reloading runtime_credentials.json each time.

        Raises:
            AlexaSessionExpiredException: If the session has expired and all
                refresh/retry attempts are exhausted.
        """
        if self._session_expiry is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        days_left = (self._session_expiry - now).days

        if self._session_expiry > now:
            if days_left <= 30:
                _warn_cooldown = 5
                if (
                    self._last_expiry_warning is None
                    or (now - self._last_expiry_warning).total_seconds() >= _warn_cooldown
                ):
                    _expiry_utc   = self._session_expiry.strftime("%Y-%m-%d %H:%M:%S UTC")
                    _expiry_local = self._session_expiry.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    _days_str = "less than a day" if days_left == 0 else f"{days_left} day{'s' if days_left != 1 else ''}"
                    logger.warning(
                        "Alexa session expires in %s on %s (%s) — "
                        "re-run %s -m pyalexalist.get_alexa_cookies soon",
                        _days_str, _expiry_utc, _expiry_local, _PYTHON_CMD,
                    )
                    self._last_expiry_warning = now
            return

        # Session is expired — try automatic token refresh first
        logger.warning("Alexa session expired — attempting automatic token refresh")
        if self._refresh_session():
            logger.info("Alexa session automatically refreshed via token exchange")
            return

        # Auto-refresh unavailable or failed — fall back to manual retry loop
        if self._cookie_expiry_retries == 0:
            raise AlexaSessionExpiredException(self._session_expiry)

        logger.warning(
            "Auto-refresh failed — run %s -m pyalexalist.get_alexa_cookies to refresh. "
            "Retrying %d time(s) every %ds.",
            _PYTHON_CMD, self._cookie_expiry_retries, self._retry_interval_seconds,
        )

        _prev_value = self._raw_cookies.get(self._expiry_cookie, {}).get("Value") if self._expiry_cookie else None

        for attempt in range(1, self._cookie_expiry_retries + 1):
            logger.warning(
                "Waiting for credentials update... (attempt %d/%d)",
                attempt, self._cookie_expiry_retries,
            )
            time.sleep(self._retry_interval_seconds)
            cookies, expiry, expiry_cookie = self.load_credentials(self._runtime_creds_path)
            _new_value = cookies.get(expiry_cookie, {}).get("Value") if expiry_cookie else None
            if _new_value == _prev_value:
                logger.warning(
                    "Cookie value for '%s' unchanged after reload — only the expiry date "
                    "may have been edited manually. Re-run %s -m pyalexalist.get_alexa_cookies "
                    "to get genuine new cookies.",
                    expiry_cookie, _PYTHON_CMD,
                )
                continue
            self._session_expiry = expiry
            self._expiry_cookie = expiry_cookie
            self._raw_cookies = cookies
            self._apply_cookies(cookies)
            if self._session_expiry and self._session_expiry > datetime.datetime.now(datetime.timezone.utc):
                logger.info("Alexa session successfully refreshed — resuming")
                return

        raise AlexaSessionExpiredException(self._session_expiry)
    
    def checkSessionExpiry(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            self.check_session_expiry()
            return func(self, *args, **kwargs)
        return wrapper
   
    @checkSessionExpiry
    def createList(self, name: str) -> "dict | None":
        """Create a new list on the Alexa server.

        Args:
            name: Display name for the new list.
        Returns:
            Raw list dict (unwrapped from 'listInfo'), or None on failure.
        Raises:
            DuplicateListNameException: If a list with this name already exists.
        """
        response = self._session.post(self._endpoint_list_api, json={"listName": name})
        if not response.ok:
            error_info = self._safe_json(response, f"createList:{name}") or {}
            if error_info.get("errorType") == "DuplicateListNameException":
                raise DuplicateListNameException(name)
            logger.warning("createList HTTP %d for '%s': %s", response.status_code, name, response.text[:200])
            return None
        list_info = self._safe_json(response, f"createList:{name}")
        if not list_info:
            return None
        return list_info.get("listInfo")

    @checkSessionExpiry
    def createListItem(self, list_id: str, item_name: str, quantity: int | None = None, note: "str | None" = None) -> "dict | None":
        """Create a new item on the Alexa list and return the server response dict.

        Args:
            list_id: Server list ID to add the item to.
            item_name: Display name for the new item.
            quantity: Optional quantity; omitted from the request if None or ≤ 1.
            note: Optional note text; omitted from the request if None/empty.
        Returns:
            Raw item dict from the server, or None on failure.
        """
        uri = self._endpoint_list_api + list_id + "/items"
        attrs_to_create = []
        if quantity and quantity > 1:
            attrs_to_create.append({"type": "quantity", "value": quantity})
        if note:
            attrs_to_create.append({"type": "note", "value": note})
        request_body = {
            "items": [
                {
                    "itemType": "KEYWORD",
                    "itemName": item_name,
                    **({"itemAttributesToCreate": attrs_to_create} if attrs_to_create else {}),
                }
            ]
        }
        response = self._session.post(uri, json=request_body)
        item_info = self._safe_json(response, f"createListItem:{item_name}")
        if not item_info:
            return None
        items = item_info.get("itemInfoList", [])
        if not items:
            logger.warning("createListItem returned empty itemInfoList for '%s' (status %d): %s", item_name, response.status_code, item_info)
            return None
        return items[0]

    @checkSessionExpiry
    def getList(self, list_id: str) -> "dict | None":
        """Fetch all items in a list from the Alexa API.

        Args:
            list_id: Server list ID to fetch.
        Returns:
            Raw API response dict with 'itemInfoList', or None on failure.
        """
        payload = {
            "itemAttributesToProject": [
                "quantity",
                "note",
            ],
            "listAttributesToAggregate": [
                {"type": "listCategories"},
                {
                    "type": "shareableInfo",
                    "marketplaceId": self._marketplace_id,
                    "clientId": "AlexaApp",
                },
                {
                    "type": "keywordRecommendations",
                    "clientId": "AlexaApp",
                    "marketplaceId": self._marketplace_id,
                    "locale": self._locale,
                },
                {"type": "totalActiveItemsCount"},
                {"type": "categoryOverride"},
            ],
        }
        uri = self._endpoint_list_api + list_id + "/items/fetch?limit=100"
        response = self._session.post(uri, json=payload)
        return self._safe_json(response, f"getList:{list_id}")

    @checkSessionExpiry
    def getListItem(self) -> None:
        pass

    @checkSessionExpiry
    def getAllLists(self) -> "dict | None":
        """Fetch all lists the authenticated user owns or shares.

        Returns:
            Raw API response dict with 'listInfoList', or None on failure.
        """
        payload = {
            "listAttributesToAggregate": [
                {"type": "listCategories"},
                {"type": "categoryOverride"},
                {
                    "type": "shareableInfo",
                    "marketplaceId": self._marketplace_id,
                    "clientId": "AlexaApp",
                },
                {"type": "sharedListOwnerInfo"},
                {"type": "collaboratorInfo"},
                {"type": "totalActiveItemsCount"},
            ],
            "listOwnershipType": "OWNED_AND_SHARED_LISTS",
        }
        uri = self._endpoint_list_api + "fetch"
        response = self._session.post(uri, json=payload)
        return self._safe_json(response, "getAllLists")

    @checkSessionExpiry
    def updateList(self, list_id: str, version_num: int, attribute: "tuple | None" = None) -> "dict | None":
        """Update a single attribute on an Alexa list, e.g. archiving via listStatus.

        Args:
            list_id: Server list ID to update.
            version_num: Current list version; used by the API for optimistic locking.
            attribute: (type, value) tuple for the attribute to set, e.g. ('listStatus', 'ARCHIVED').
        Returns:
            Updated list dict (unwrapped from 'listInfo'), or None on failure.
        """
        uri = self._endpoint_list_api + list_id + f"?version={version_num}"
        attrs_to_update = []
        if attribute is not None:
            attrs_to_update.append({"type": attribute[0], "value": attribute[1]})
        request_body = {"listAttributesToUpdate": attrs_to_update}

        response = self._session.put(uri, json=request_body)
        if not response.ok:
            logger.warning("updateList HTTP %d for list %s attr=%s: %s", response.status_code, list_id, attribute, response.text[:200])
            return None
        list_info = self._safe_json(response, f"updateList:{list_id}")
        if not list_info:
            return None
        return list_info.get("listInfo")

    @checkSessionExpiry
    def updateListItem(self, list_id: str, item_id: str, version_num: int, attribute: "tuple | None" = None, remove_attributes: "list | None" = None) -> "dict | None":
        """Update a single attribute on an Alexa list item.

        Args:
            list_id: Server list ID containing the item.
            item_id: Server item ID to update.
            version_num: Current item version; used by the API for optimistic locking.
            attribute: (type, value) tuple for the attribute to set, e.g. ('itemStatus', 'COMPLETE').
            remove_attributes: Attribute type strings to remove, e.g. ['quantity'].
        Returns:
            Updated item dict (unwrapped from 'itemInfo'), or None on failure.
        """
        uri = self._endpoint_list_api + list_id + "/items/" + item_id + f"?version={version_num}"
        attrs_to_update = []
        if attribute is not None:
            attrs_to_update.append({"type": attribute[0], "value": attribute[1]})
        request_body = {
            "itemAttributesToUpdate": attrs_to_update,
            "itemAttributesToRemove": list(remove_attributes or []),
        }
        
        response = self._session.put(uri, json=request_body)
        if not response.ok:
            logger.warning("updateListItem HTTP %d for item %s attr=%s: %s", response.status_code, item_id, attribute, response.text[:200])
            return None
        item_info = self._safe_json(response, f"updateListItem:{item_id}")
        if not item_info:
            return None
        return item_info.get("itemInfo")

    @checkSessionExpiry
    def deleteList(self, list_id: str, version_num: int) -> "dict | str":
        """Delete a list from Alexa.

        Args:
            list_id: Server list ID to delete.
            version_num: Current list version for optimistic locking.
        Returns:
            Server response dict, or an empty string if the response body was empty.
        """
        uri = self._endpoint_list_api + list_id + f"?version={version_num}"
        response = self._session.delete(uri)
        content = response.content.decode()
        if content:
            return json.loads(content)
        return content

    @checkSessionExpiry
    def deleteListItem(self, list_id: str, item_id: str, version_num: int) -> "dict | str":
        """Delete an item from an Alexa list.

        Args:
            list_id: Server list ID containing the item.
            item_id: Server item ID to delete.
            version_num: Current item version for optimistic locking.
        Returns:
            Server response dict, or an empty string if the response body was empty.
        """
        uri = self._endpoint_list_api + list_id + "/items/" + item_id + f"?version={version_num}"      
        response = self._session.delete(uri)
        content = response.content.decode()
        if content:
            item_info = json.loads(content)
            return item_info
        return content
    
    # Cleans up the namespace to reduce clutter 
    del checkSessionExpiry

    def updateHeader(self) -> None:

        self._session.headers.update(
            {
                "Accept": "application/json; charset=utf-8",
                "Accept-Encoding": "gzip",
                "Accept-Language": "en-US",
                "Cache-Control": "no-cache",
                "Connection": "Keep-Alive",
                "Content-Type": "application/json; charset=utf-8",
                "Host": self._www_domain,
                "User-Agent": "PitanguiBridge/2.2.605806.0-[PLATFORM=Android][MANUFACTURER=Google][RELEASE=11][BRAND=google][SDK=30][MODEL=sdk_gphone_x86]",
            }
        )

    def _apply_cookies(self, client_info: dict) -> None:
        s = self._cookie_suffix
        # x-main and session-token are consistent across all Amazon regions.
        # ubid-*, at-*, sess-at-* use the region-specific suffix (e.g. "main", "acbuk", "acbde").
        # lc-main controls the Amazon website display language — set to the region locale.
        self._session.cookies.update(
            {
                "sid": "",
                "lc-main": self._locale,
                f"ubid-{s}": client_info[f"ubid-{s}"]["Value"],
                "session-token": client_info["session-token"]["Value"],
                "x-main": client_info["x-main"]["Value"],
                f"at-{s}": client_info[f"at-{s}"]["Value"],
                f"sess-at-{s}": client_info[f"sess-at-{s}"]["Value"],
            }
        )

class AlexaList:
    """High-level Alexa list manager — owns local list state and coordinates pull/push with AlexaAPI."""
    #TODO maybe a clear method to reset all lists to nothing/?
    #TODO parse user info???

    def __init__(self, cookie_expiry_retries: int = 0, retry_interval_seconds: int = 30, amazon_domain: str = "amazon.com", service_auth_path: "Path | str | None" = None) -> None:
        self.alexa_api = AlexaAPI(cookie_expiry_retries=cookie_expiry_retries, retry_interval_seconds=retry_interval_seconds, amazon_domain=amazon_domain, service_auth_path=service_auth_path)
        self._lists = {}

    def authenticate(self) -> None:
        """Reload session credentials from disk."""
        self.alexa_api.updateCookies()

    def get(self, name: str | None = None, *, id: str | None = None, list_id: str | None = None) -> List | None:
        """Look up a single list by name, internal UUID, or server list_id.

        Args:
            name: Match by list name.
            id: Match by internal UUID (keyword-only).
            list_id: Match by server-assigned listId (keyword-only).
        Returns:
            The first matching List, or None.
        """
        if name is not None:
            return next((lst for lst in self._lists.values() if lst.listName == name), None)
        if id is not None:
            return next((lst for lst in self._lists.values() if lst.id == id), None)
        if list_id is not None:
            return next((lst for lst in self._lists.values() if lst.listId == list_id), None)
        return None

    def all(self) -> list[List]:
        """Get all lists."""
        return list(self._lists.values())

    def find(self, query: str | None = None, func=None) -> list[List]:
        """Find lists matching the given criteria.

        Args:
            query: Case-insensitive substring match against list name.
            func: A filter function applied to each List object.
        """
        return [
            lst for lst in self._lists.values()
            if (query is None or query.casefold() in lst.listName.casefold())
            and (func is None or func(lst))
        ]
        #TODO double check with gkeep about generators?

    def createList(self, name: str) -> List:
        """Create a new list on the server and register it locally.

        Args:
            name: Display name for the new list.
        Returns:
            The newly created List.
        Raises:
            RuntimeError: If the server request failed.
        """
        raw = self.alexa_api.createList(name)
        if not raw:
            raise RuntimeError(f"createList failed for '{name}'")
        lst = List()
        lst.load(raw)
        self._lists[lst.id] = lst
        return lst

    def deleteList(self, name: str) -> None:
        """Delete a list from the server and remove it locally.

        Args:
            name: Display name of the list to delete.
        Raises:
            DefaultListModificationException: If name is one of Alexa's built-in SHOP/TODO lists.
        """
        lst = self.get(name)
        if not lst:
            return
        if not lst.isCustom:
            raise DefaultListModificationException(name, "delete")
        self.alexa_api.deleteList(lst.listId, lst.version)
        del self._lists[lst.id]

    def resync(self) -> None:
        """Discard all local state and rebuild from a full server fetch."""
        raw = self.alexa_api.getAllLists()
        if not raw:
            return
        self._lists = {}
        for raw_list in raw["listInfoList"]:
            lst = List()
            lst.load(raw_list)
            self._lists[lst.id] = lst

            raw_items = self.alexa_api.getList(lst.listId)
            if not raw_items or "itemInfoList" not in raw_items:
                continue

            for raw_item in raw_items["itemInfoList"]:
                item = ListItem()
                item.load(raw_item)
                lst._items[item.id] = item

    def pull(self, force: bool = False) -> None:
        """Fetch current server state — non-destructive, preserves unsynced local items.

        Args:
            force: If True, overwrite dirty local items with server state (discards pending changes).
        """
        raw = self.alexa_api.getAllLists()
        if not raw:
            return

        seen_list_ids = {raw_list["listId"] for raw_list in raw["listInfoList"]}
        for stale_lst in [l for l in self._lists.values() if l.listId is not None and l.listId not in seen_list_ids]:
            del self._lists[stale_lst.id]

        for raw_list in raw["listInfoList"]:
            list_id = raw_list["listId"]

            lst = self.get(list_id=list_id)
            if lst is None:
                lst = List()
                self._lists[lst.id] = lst
            lst.load(raw_list)

            raw_items = self.alexa_api.getList(list_id)
            if not raw_items or "itemInfoList" not in raw_items:
                continue

            seen_item_ids = {i["itemId"] for i in raw_items["itemInfoList"]}
            for item in [i for i in lst.items if i.itemId is not None and i.itemId not in seen_item_ids]:
                lst.remove(item)

            for raw_item in raw_items["itemInfoList"]:
                existing = lst.get(item_id=raw_item["itemId"])
                if existing:
                    if not force and existing.dirty:
                        if raw_item["version"] != existing.version:
                            logger.warning("pull conflict on '%s' — local changes pending but server version changed", existing.itemName)
                    else:
                        existing.load(raw_item)
                else:
                    item = ListItem()
                    item.load(raw_item)
                    lst._items[item.id] = item

    def push(self, force: bool = False) -> None:
        """Send pending local changes (creates, deletes, dirty fields) to Alexa.

        Args:
            force: If True, skip snapshot comparison and push all dirty fields even if the
                   value matches the last-known server state.
        """
        for lst in self._lists.values():
            if not lst.dirty:
                continue
            list_id = lst.listId
            if list_id is None:
                continue
            if lst.dirty_fields:
                for attribute in list(lst.dirty_fields):
                    match attribute:
                        case "listName":
                            if not force and lst.listName == lst._server_listName:
                                lst.dirty_fields.discard("listName")
                                continue
                            attr = ("listName", lst.listName)
                            new_list_info = self.alexa_api.updateList(list_id, lst.version, attr)
                            if new_list_info:
                                lst.load(new_list_info)
                            else:
                                logger.warning("push: updateList listName failed for '%s'", lst.listName)
                        case "listStatus":
                            new_status = "ARCHIVED" if lst.archived else "ACTIVE"
                            if not force and new_status == lst._server_listStatus:
                                lst.dirty_fields.discard("listStatus")
                                continue
                            attr = ("listStatus", new_status)
                            new_list_info = self.alexa_api.updateList(list_id, lst.version, attr)
                            if new_list_info:
                                lst.load(new_list_info)
                            else:
                                logger.warning("push: updateList listStatus failed for '%s'", lst.listName)
            for item in lst.items:
                if item.deleted and item.itemId is None:
                    lst.remove(item)

                elif item.deleted:
                    self.alexa_api.deleteListItem(list_id, item.itemId, item.version)
                    lst.remove(item)
                elif item.itemId is None:
                    raw = self.alexa_api.createListItem(list_id, item.itemName, item.quantity, item.note)
                    if raw:
                        item.load(raw)
                elif item.dirty_fields:
                    for attribute in list(item.dirty_fields):
                        match attribute:
                            case "itemName":
                                if not force and item.itemName == item._server_itemName:
                                    item.dirty_fields.discard("itemName")
                                    continue
                                attr = ("itemName", item.itemName)
                                new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, attr)
                                if new_item_info:
                                    item.load(new_item_info)
                                else:
                                    logger.warning("push: updateListItem itemName failed for '%s'", item.itemName)
                            case "itemStatus":
                                if not force and item.checked == item._server_itemStatus:
                                    item.dirty_fields.discard("itemStatus")
                                    continue
                                attr = ("itemStatus", item.checked.label)
                                new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, attr)
                                if new_item_info:
                                    item.load(new_item_info)
                                else:
                                    logger.warning("push: updateListItem itemStatus failed for '%s' (tried: %s)", item.itemName, item.checked.label)
                            case "quantity":
                                if not force and item.quantity == item._server_quantity:
                                    item.dirty_fields.discard("quantity")
                                    continue
                                if item.quantity is None or item.quantity < 2:
                                    new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, remove_attributes=["quantity"])
                                else:
                                    attr = ("quantity", item.quantity)
                                    new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, attr)
                                if new_item_info:
                                    item.load(new_item_info)
                                else:
                                    logger.warning("push: updateListItem quantity failed for '%s'", item.itemName)
                            case "note":
                                if not force and item.note == item._server_note:
                                    item.dirty_fields.discard("note")
                                    continue
                                if not item.note:
                                    new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, remove_attributes=["note"])
                                else:
                                    attr = ("note", item.note)
                                    new_item_info = self.alexa_api.updateListItem(list_id, item.itemId, item.version, attr)
                                if new_item_info:
                                    item.load(new_item_info)
                                else:
                                    logger.warning("push: updateListItem note failed for '%s'", item.itemName)

    def sync(self, force: bool = False) -> None:
        """Pull fresh server state then push pending local changes.

        Args:
            force: Passed to both pull and push; see each method for details.
        """
        self.pull(force=force)
        self.push(force=force)
