import asyncio
import re
import time
from collections.abc import Callable
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import TypedDict
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup

FM_BASE_URL = "https://www.fmkorea.com"
FM_LIST_URL = f"{FM_BASE_URL}/best"
DC_BASE_URL = "https://gall.dcinside.com"
DC_LIST_URL = f"{DC_BASE_URL}/board/lists/?id=dcbest"
CACHE_SECONDS = 300
ERROR_COOLDOWN_SECONDS = 60


class Post(TypedDict):
    title: str
    source: str
    url: str


class PostFetchError(Exception):
    """Collecting errors that can be reported to Discord users"""


def _fmkorea_post_url(href: str) -> str | None:
    # Arrange relative and query URLs into the same post URL.
    try:
        parsed = urlsplit(urljoin(FM_BASE_URL, href))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname not in {"fmkorea.com", "www.fmkorea.com"}:
        return None

    post_id = parse_qs(parsed.query).get("document_srl", [""])[0]
    if not post_id:
        match = re.fullmatch(r"/(?:best2?/)?([0-9]+)/?", parsed.path)
        post_id = match.group(1) if match else ""
    if not re.fullmatch(r"[0-9]+", post_id) or int(post_id) == 0:
        return None
    return f"{FM_BASE_URL}/{int(post_id)}"


def parse_fmkorea_posts(html: str) -> list[Post]:
    # parse title links
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    seen: set[str] = set()

    for anchor in soup.select(".fm_best_widget h3.title > a[href]"):
        item = anchor.find_parent("li")
        if item is None or "notice" in item.get("class", []):
            continue

        url = _fmkorea_post_url(anchor["href"])
        if url is None or url in seen:
            continue

        for comment_count in anchor.select(".comment_count"):
            comment_count.decompose()
        title = " ".join(anchor.stripped_strings)
        if not title:
            continue

        seen.add(url)
        posts.append({"title": title[:200], "source": "에펨코리아", "url": url})

    return posts


def _dcinside_post_url(href: str) -> str | None:
    # parse post URL from relative and query URLs. Only allow gall.dcinside.com/view/ with id=dcbest and a numeric no= parameter.
    try:
        parsed = urlsplit(urljoin(DC_BASE_URL, href))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "gall.dcinside.com":
        return None
    if parsed.path != "/board/view/":
        return None

    query = parse_qs(parsed.query)
    if query.get("id") != ["dcbest"]:
        return None
    post_id = query.get("no", [""])[0]
    if not re.fullmatch(r"[0-9]+", post_id) or int(post_id) == 0:
        return None
    return f"{DC_BASE_URL}/board/view/?" + urlencode({"id": "dcbest", "no": str(int(post_id))})


def parse_dcinside_posts(html: str) -> list[Post]:
    # parse title links from the DCinside. Exclude notice rows and rows with non-numeric numbers (e.g., 공지, 설문, 광고).
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    seen: set[str] = set()

    for row in soup.select("table.gall_list tbody tr.ub-content"):
        if row.get("data-type") == "icon_notice" or "notice" in row.get("class", []):
            continue
        number_cell = row.select_one("td.gall_num")
        number = number_cell.get_text(strip=True) if number_cell else ""
        # Exclude rows with non-numeric numbers (e.g., 공지, 설문, 광고).
        if not re.fullmatch(r"[0-9]+", number):
            continue

        for anchor in row.select("td.gall_tit a[href]:not(.reply_numbox)"):
            url = _dcinside_post_url(anchor["href"])
            if url is None or url in seen:
                continue
            title = " ".join(anchor.stripped_strings)
            if not title:
                continue
            seen.add(url)
            posts.append({"title": title[:200], "source": "디시인사이드", "url": url})
            break

    return posts


def _retry_seconds(value: str | None) -> float:
    # Parse the Retry-After header into seconds.
    if value:
        if value.isascii() and value.isdecimal():
            return max(ERROR_COOLDOWN_SECONDS, int(value))
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(ERROR_COOLDOWN_SECONDS, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            pass
    return ERROR_COOLDOWN_SECONDS


class CachedPostSource:
    # HTTP request, cache, and retry handling shared by both sites.
    def __init__(self, name: str, list_url: str, parser: Callable[[str], list[Post]]):
        self.name = name
        self.list_url = list_url
        self.parser = parser
        # Each instance has its own values, thus two caches for the two sites do not mix.
        self._session: aiohttp.ClientSession | None = None
        self._posts: list[Post] = []
        self._expires_at = 0.0
        self._retry_at = 0.0
        self._lock = asyncio.Lock()

    async def get_posts(self) -> list[Post]:
        # If multiple users call this at the same time, only one request is made to the site.
        async with self._lock:
            now = time.monotonic()
            if self._posts and now < self._expires_at:
                return list(self._posts)
            if now < self._retry_at:
                raise PostFetchError(f"{self.name} listing is temporarily unavailable. Please try again later.")

            self._retry_at = now + ERROR_COOLDOWN_SECONDS
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "DiscordHotpostBot/0.1 (personal learning project)"},
                )

            try:
                async with self._session.get(self.list_url, allow_redirects=False) as response:
                    if response.status == 429:
                        self._retry_at = time.monotonic() + _retry_seconds(
                            response.headers.get("Retry-After")
                        )
                        raise PostFetchError(f"{self.name} is temporarily unavailable. Please try again later.")
                    if response.status != 200:
                        raise PostFetchError(f"{self.name} is not accessible. (HTTP {response.status})")
                    if response.content_type != "text/html":
                        raise PostFetchError(f"{self.name} did not return the expected list page.")
                    html = await response.text()
            except asyncio.TimeoutError as error:
                raise PostFetchError(f"{self.name} response timed out. Please try again later.") from error
            except aiohttp.ClientError as error:
                raise PostFetchError(f"{self.name} failed to connect. Please try again later.") from error

            posts = self.parser(html)
            if not posts:
                raise PostFetchError(f"{self.name} did not find any posts. Please check the site structure or access status.")
            self._posts = posts
            self._expires_at = time.monotonic() + CACHE_SECONDS
            self._retry_at = 0.0
            return list(posts)

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None


class FmkoreaSource(CachedPostSource):
    def __init__(self):
        super().__init__("에펨코리아", FM_LIST_URL, parse_fmkorea_posts)


class DcinsideSource(CachedPostSource):
    def __init__(self):
        super().__init__("디시인사이드 실시간 베스트", DC_LIST_URL, parse_dcinside_posts)
