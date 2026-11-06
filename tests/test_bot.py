import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hot_posts import PostFetchError


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 실제 .env를 읽거나 Discord에 연결하지 않습니다.
        with patch.dict(
            os.environ,
            {"DISCORD_TOKEN": "test-placeholder", "DISCORD_GUILD_ID": "123456789012345678"},
        ), patch("dotenv.load_dotenv", return_value=False):
            self.module = runpy.run_path(
                str(Path(__file__).resolve().parents[1] / "bot.py"),
                run_name="test_bot_module",
            )
        self.bot = self.module["bot"]
        self.commands = {
            command.name: command
            for command in self.bot.tree.get_commands(guild=self.module["test_guild"])
        }
        self.interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.posts = {
            "fmkorea": [{"title": "테스트 글", "source": "에펨코리아", "url": "https://www.fmkorea.com/123"}],
            "dcinside": [{"title": "실베 글", "source": "디시인사이드", "url": "https://gall.dcinside.com/board/view/?id=dcbest&no=456"}],
        }
        self.fetchers = {}
        for key, source in self.bot.post_sources.items():
            patcher = patch.object(source, "get_posts", new_callable=AsyncMock)
            self.fetchers[key] = patcher.start()
            self.fetchers[key].return_value = self.posts[key]
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_top_post_defers_before_fetch_and_sends_a_post_url(self):
        async def fetch():
            self.interaction.response.defer.assert_awaited_once_with(thinking=True)
            return self.posts["fmkorea"]

        self.fetchers["fmkorea"].side_effect = fetch
        await self.commands["top-post"].callback(self.interaction, site="fmkorea")
        self.interaction.followup.send.assert_awaited_once()
        call = self.interaction.followup.send.call_args
        self.assertEqual(call.args[0], "**테스트 글**\n<https://www.fmkorea.com/123>")
        self.assertFalse(call.kwargs["allowed_mentions"].everyone)
        self.fetchers["dcinside"].assert_not_awaited()

    async def test_fetch_failure_returns_a_message(self):
        self.fetchers["fmkorea"].side_effect = PostFetchError("조회 실패")
        await self.commands["top-post"].callback(self.interaction, site="fmkorea")
        self.assertEqual(self.interaction.followup.send.call_args.args[0], "조회 실패")
        self.fetchers["dcinside"].assert_not_awaited()

    async def test_explicit_dcinside_sends_a_dcinside_post(self):
        await self.commands["top-post"].callback(self.interaction, site="dcinside")
        message = self.interaction.followup.send.call_args.args[0]
        self.assertEqual(message, "**실베 글**\n<https://gall.dcinside.com/board/view/?id=dcbest&no=456>")
        self.fetchers["fmkorea"].assert_not_awaited()

    async def test_random_mode_uses_the_randomized_site_order(self):
        with patch.object(self.module["random"], "shuffle", side_effect=lambda keys: keys.reverse()) as shuffle:
            await self.commands["top-post"].callback(self.interaction)
        shuffle.assert_called_once()
        self.fetchers["dcinside"].assert_awaited_once()
        self.fetchers["fmkorea"].assert_not_awaited()

    async def test_random_mode_tries_the_other_site_after_a_failure(self):
        self.fetchers["fmkorea"].side_effect = PostFetchError("에펨코리아 조회 실패")
        with patch.object(self.module["random"], "shuffle", return_value=None):
            await self.commands["top-post"].callback(self.interaction)
        self.fetchers["fmkorea"].assert_awaited_once()
        self.fetchers["dcinside"].assert_awaited_once()
        self.assertIn(self.posts["dcinside"][0]["url"], self.interaction.followup.send.call_args.args[0])

    async def test_random_mode_reports_both_failures(self):
        self.fetchers["fmkorea"].side_effect = PostFetchError("에펨코리아 조회 실패")
        self.fetchers["dcinside"].side_effect = PostFetchError("디시 조회 실패")
        with patch.object(self.module["random"], "shuffle", return_value=None):
            await self.commands["top-post"].callback(self.interaction)
        self.assertEqual(self.interaction.followup.send.call_args.args[0], "에펨코리아 조회 실패\n디시 조회 실패")

    async def test_site_option_is_optional_and_offers_three_choices(self):
        parameter = self.commands["top-post"].parameters[0]
        self.assertEqual(parameter.name, "site")
        self.assertFalse(parameter.required)
        self.assertEqual(parameter.default, "random")
        self.assertEqual([choice.value for choice in parameter.choices], ["random", "fmkorea", "dcinside"])

    async def test_ping_still_responds(self):
        await self.commands["ping"].callback(self.interaction)
        self.interaction.response.send_message.assert_awaited_once_with("Pong!")


if __name__ == "__main__":
    unittest.main()
