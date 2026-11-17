"""Render pages in headless Chromium (Playwright) so job lists loaded by
JavaScript become visible. Also records the URLs the page requested while
loading, which often reveal the ATS behind an embedded job widget.

render_many(urls) -> {url: Rendered}
"""
import asyncio
from dataclasses import dataclass, field

from netutil import UA


@dataclass
class Rendered:
    status: int
    final_url: str
    html: str
    requests: list[str] = field(default_factory=list)


async def _render_one(browser, url, sem, timeout_ms):
    async with sem:
        ctx = await browser.new_context(user_agent=UA, locale="en-US")
        page = await ctx.new_page()
        reqs: list[str] = []
        page.on("request", lambda r: reqs.append(r.url))
        # Skip heavy assets: we only need the DOM text.
        await page.route("**/*", lambda route: route.abort()
                         if route.request.resource_type in ("image", "media", "font") else route.continue_())
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # some pages never go idle (analytics, chat widgets)
            # Expand lazy content a bit.
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(1200)
            html = await page.content()
            frames = [await f.content() for f in page.frames[1:] if f.url.startswith("http")]
            html += "\n".join(frames[:5])
            return url, Rendered(resp.status if resp else 0, page.url, html, reqs)
        except Exception as e:
            return url, Rendered(0, url, f"{type(e).__name__}: {e}", reqs)
        finally:
            await ctx.close()


async def _render_many(urls, concurrency, timeout_ms):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        sem = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(*[_render_one(browser, u, sem, timeout_ms) for u in urls])
        await browser.close()
    return dict(results)


def render_many(urls, concurrency: int = 6, timeout_ms: int = 25000) -> dict[str, Rendered]:
    urls = list(dict.fromkeys(urls))
    if not urls:
        return {}
    return asyncio.run(_render_many(urls, concurrency, timeout_ms))


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False
