import asyncio
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from hot_posts import (
    CACHE_SECONDS,
    DC_LIST_URL,
    FM_LIST_URL,
    DcinsideSource,
    FmkoreaSource,
    PostFetchError,
    _retry_seconds,
    parse_dcinside_posts,
    parse_fmkorea_posts,
)


# 직접 만든 HTML로 실제 사이트에 접속하지 않고 파서를 검증합니다.
LIST_HTML = """
<nav><a href="/999">메뉴 링크</a></nav>
<div class="fm_best_widget">
  <ul>
    <li class="notice"><h3 class="title"><a href="/888">공지</a></h3></li>
    <li><h3 class="title"><a href="/123">
      <span class="ellipsis-target">첫 번째 글</span><span class="comment_count">[42]</span>
    </a></h3></li>
    <li><h3 class="title"><a href="/index.php?mid=best&amp;document_srl=123">중복 글</a></h3></li>
    <li><h3 class="title"><a href="/index.php?mid=best&amp;document_srl=456">두 번째 글</a></h3></li>
    <li><h3 class="title"><a href="https://unrelated.example/789">외부 광고</a></h3></li>
    <li><h3 class="title"><a href="https://[invalid">잘못된 주소</a></h3></li>
    <li><h3 class="title"><a href="/best">목록 링크</a></h3></li>
    <li><h3 class="title"><a href="/777"><span class="comment_count">[1]</span></a></h3></li>
  </ul>
</div>
"""

DC_LIST_HTML = """
<nav><a href="/board/view/?id=dcbest&amp;no=999">목록 밖 링크</a></nav>
<table class="gall_list"><tbody>
  <tr class="ub-content" data-type="icon_notice">
    <td class="gall_num">100</td>
    <td class="gall_tit"><a href="/board/view/?id=dcbest&amp;no=100">숫자가 있어도 공지</a></td>
  </tr>
  <tr class="ub-content">
    <td class="gall_num">설문</td>
    <td class="gall_tit"><a href="/board/view/?id=dcbest&amp;no=101">설문 링크</a></td>
  </tr>
  <tr class="ub-content us-post">
    <td class="gall_num">123</td>
    <td class="gall_tit">
      <a href="/board/view/?id=dcbest&amp;no=123&amp;page=1"><em></em><strong>[예시]</strong> 첫 글</a>
      <a class="reply_numbox" href="/board/view/?id=dcbest&amp;no=123&amp;t=cv">[42]</a>
    </td>
  </tr>
  <tr class="ub-content">
    <td class="gall_num">123</td>
    <td class="gall_tit"><a href="https://gall.dcinside.com/board/view/?no=123&amp;id=dcbest">중복</a></td>
  </tr>
  <tr class="ub-content">
    <td class="gall_num">456</td>
    <td class="gall_tit"><a href="/board/view/?id=dcbest&amp;no=456&amp;_dcbest=1">두 번째 글</a></td>
  </tr>
</tbody></table>
"""


class ParserTests(unittest.TestCase):
    def test_extracts_posts_and_excludes_navigation_notices_duplicates_and_ads(self):
        self.assertEqual(
            parse_fmkorea_posts(LIST_HTML),
            [
                {"title": "첫 번째 글", "source": "에펨코리아", "url": "https://www.fmkorea.com/123"},
                {"title": "두 번째 글", "source": "에펨코리아", "url": "https://www.fmkorea.com/456"},
            ],
        )

    def test_block_page_contains_no_posts(self):
        self.assertEqual(parse_fmkorea_posts("<html><h1>Access denied</h1></html>"), [])

    def test_retry_after_supports_seconds_and_dates(self):
        self.assertEqual(_retry_seconds("120"), 120)
        self.assertEqual(_retry_seconds("invalid"), 60)
        with patch("hot_posts.time.time", return_value=0):
            self.assertEqual(_retry_seconds("Thu, 01 Jan 1970 00:02:00 GMT"), 120)


class DcinsideParserTests(unittest.TestCase):
    def test_extracts_real_posts_without_notices_polls_comments_or_duplicates(self):
        self.assertEqual(
            parse_dcinside_posts(DC_LIST_HTML),
            [
                {"title": "[예시] 첫 글", "source": "디시인사이드", "url": "https://gall.dcinside.com/board/view/?id=dcbest&no=123"},
                {"title": "두 번째 글", "source": "디시인사이드", "url": "https://gall.dcinside.com/board/view/?id=dcbest&no=456"},
            ],
        )

    def test_rejects_unrelated_galleries_hosts_and_malformed_urls(self):
        urls = [
            "https://unrelated.example/board/view/?id=dcbest&no=123",
            "/board/view/?id=other_gallery&no=123",
            "/board/lists/?id=dcbest",
            "https://[invalid",
            "javascript:alert(1)",
            "/board/view/?id=dcbest&no=not-a-number",
        ]
        for url in urls:
            with self.subTest(url=url):
                html = f'<table class="gall_list"><tbody><tr class="ub-content"><td class="gall_num">123</td><td class="gall_tit"><a href="{url}">글</a></td></tr></tbody></table>'
                self.assertEqual(parse_dcinside_posts(html), [])

    def test_empty_title_is_not_replaced_by_a_comment_count(self):
        html = """<table class="gall_list"><tbody><tr class="ub-content">
          <td class="gall_num">123</td><td class="gall_tit">
          <a href="/board/view/?id=dcbest&amp;no=123"></a>
          <a class="reply_numbox" href="/board/view/?id=dcbest&amp;no=123">[42]</a>
          </td></tr></tbody></table>"""
        self.assertEqual(parse_dcinside_posts(html), [])

    def test_block_page_contains_no_posts(self):
        self.assertEqual(parse_dcinside_posts("<html>Access denied</html>"), [])


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.response = MagicMock()
        self.response.status = 200
        self.response.content_type = "text/html"
        self.response.headers = {}
        self.response.text = AsyncMock(return_value=LIST_HTML)
        self.response.__aenter__ = AsyncMock(return_value=self.response)
        self.response.__aexit__ = AsyncMock(return_value=False)
        self.session = MagicMock()
        self.session.get.return_value = self.response
        self.session.close = AsyncMock()

        session_patch = patch("hot_posts.aiohttp.ClientSession", return_value=self.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        clock_patch = patch(
            "hot_posts.time",
            SimpleNamespace(monotonic=MagicMock(return_value=1000.0), time=time.time),
        )
        self.clock = clock_patch.start().monotonic
        self.addCleanup(clock_patch.stop)
        self.source = FmkoreaSource()

    async def asyncTearDown(self):
        await self.source.close()

    async def test_cache_is_shared_and_refreshes_after_expiry(self):
        first = await self.source.get_posts()
        second = await self.source.get_posts()
        self.assertEqual(first, second)
        self.assertEqual(self.session.get.call_count, 1)
        self.clock.return_value += CACHE_SECONDS + 1
        await self.source.get_posts()
        self.assertEqual(self.session.get.call_count, 2)

    async def test_sites_fetch_their_own_lists_and_keep_separate_caches(self):
        fm_posts = await self.source.get_posts()
        self.session.get.assert_called_with(FM_LIST_URL, allow_redirects=False)
        dc_source = DcinsideSource()
        try:
            self.response.text.return_value = DC_LIST_HTML
            dc_posts = await dc_source.get_posts()
            self.session.get.assert_called_with(DC_LIST_URL, allow_redirects=False)
            self.assertEqual(dc_posts[0]["source"], "디시인사이드")
            self.assertEqual(await self.source.get_posts(), fm_posts)
            self.assertEqual(await dc_source.get_posts(), dc_posts)
            self.assertEqual(self.session.get.call_count, 2)
        finally:
            await dc_source.close()

    async def test_concurrent_commands_share_one_request(self):
        results = await asyncio.gather(*(self.source.get_posts() for _ in range(5)))
        self.assertTrue(all(len(posts) == 2 for posts in results))
        self.assertEqual(self.session.get.call_count, 1)

    async def test_429_honors_retry_after(self):
        self.response.status = 429
        self.response.headers = {"Retry-After": "120"}
        with self.assertRaisesRegex(PostFetchError, "temporarily unavailable"):
            await self.source.get_posts()
        self.clock.return_value += 61
        with self.assertRaises(PostFetchError):
            await self.source.get_posts()
        self.assertEqual(self.session.get.call_count, 1)
        self.clock.return_value += 60
        self.response.status = 200
        self.assertEqual(len(await self.source.get_posts()), 2)
        self.assertEqual(self.session.get.call_count, 2)

    async def test_timeout_has_a_cooldown(self):
        self.session.get.side_effect = asyncio.TimeoutError()
        with self.assertRaisesRegex(PostFetchError, "response timed out"):
            await self.source.get_posts()
        with self.assertRaises(PostFetchError):
            await self.source.get_posts()
        self.assertEqual(self.session.get.call_count, 1)

    async def test_connection_error_is_reported(self):
        self.session.get.side_effect = aiohttp.ClientConnectionError()
        with self.assertRaisesRegex(PostFetchError, "failed to connect"):
            await self.source.get_posts()

    async def test_denied_request_is_reported_without_reading_body(self):
        self.response.status = 403
        with self.assertRaisesRegex(PostFetchError, "HTTP 403"):
            await self.source.get_posts()
        self.response.text.assert_not_awaited()

    async def test_unexpected_content_type_is_rejected(self):
        self.response.content_type = "application/json"
        with self.assertRaisesRegex(PostFetchError, "expected list page"):
            await self.source.get_posts()

    async def test_empty_page_does_not_return_the_site_homepage(self):
        self.response.text.return_value = "<html>no posts</html>"
        with self.assertRaisesRegex(PostFetchError, "did not find any posts"):
            await self.source.get_posts()


if __name__ == "__main__":
    unittest.main()
