# discord-hotpost-bot
A Discord bot that shares trending posts from Korean online communities.

Currently in early development. Sources: FM Korea popular posts and DCInside real-time best.

## Commands

| Command | Description |
| --- | --- |
| `/ping` | Check whether the bot is responsive. |
| `/top-post` | Randomly choose a source, then send one post's title and direct URL. |
| `/top-post site:에펨코리아` | Select FM Korea explicitly using the `site` option. |
| `/top-post site:디시인사이드 실시간 베스트` | Select DCInside real-time best using the `site` option. |

The optional `site` menu also offers `랜덤` (random). In random mode each site has
an equal chance of being tried first; if it fails, the other site is tried. An
explicit site choice never silently switches sites.

## Local setup

Developed with Python 3.14.3. Dependency versions are pinned in `requirements.txt`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `DISCORD_TOKEN` and `DISCORD_GUILD_ID` in `.env`. The guild ID must belong to
the test server where this bot is installed. Keep `.env` out of Git.

```bash
python bot.py
```

Both commands are registered to the configured test server when the bot starts.
Stop the process with Control+C before restarting it after code changes.

## How popular posts are selected

1. `bot.py` selects a source based on the command's `site` option.
2. `hot_posts.py` fetches that source's list if its cache has expired:
   - FM Korea: `https://www.fmkorea.com/best`
   - DCInside: `https://gall.dcinside.com/board/lists/?id=dcbest`
3. A site-specific BeautifulSoup parser extracts post-title links, excluding
   navigation, notices, polls, comment-count links, and duplicate post IDs.
   Individual post pages, comments, and author profiles are not fetched.
4. Each source keeps its own five-minute in-memory cache, shared across commands.
5. `bot.py` selects one post from that list and sends its title, source, and URL.

The candidate pool is the selected source's current first listing page, not its entire history.
The same post may be selected consecutively. The cache resets when the bot restarts.

Requests time out after ten seconds. After a failure, the next request cannot start
until at least one minute after the previous attempt began; HTTP 429 responses
also honor `Retry-After`. If the site blocks access
or changes its HTML, the command reports an error instead of returning a homepage
or a sample link.

FM Korea's [robots.txt](https://www.fmkorea.com/robots.txt) currently allows `/best`
for generic bots. DCInside's [robots.txt](https://gall.dcinside.com/robots.txt) has
a generic allow rule with exceptions that do not currently list `dcbest`, while its
[terms](https://nstatic.dcinside.com/dc/w/policy/policy_index.html) state that crawling
requires prior written consent. Technical access and permission are separate;
recheck source policies before expanding collection or deploying.

## 코드 설명

처음 코드를 읽으신다면 [한국어 코드 설명](docs/code-walkthrough.md)을 참고하세요.
에펨코리아 수집 코드부터 디시인사이드 추가 부분, 비동기 처리와 캐시를 설명합니다.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic HTML and mocked requests. They do not use the real bot token,
contact Discord, or fetch community pages.
