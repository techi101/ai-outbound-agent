"""Browser walk-through of the recorded example at desktop and phone width.

Skipped when Playwright or a browser is not installed. Audio is muted and
speech synthesis stubbed, so a test run never makes a sound.
"""

import os
from pathlib import Path
import socket
import threading
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # optional dependency
    sync_playwright = None

import uvicorn

import server

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.srv = uvicorn.Server(uvicorn.Config(server.app, port=cls.port, log_level="error"))
        threading.Thread(target=cls.srv.run, daemon=True).start()
        for _ in range(100):
            if cls.srv.started:
                break
            time.sleep(0.05)
        cls.pw = sync_playwright().start()
        kwargs = {"headless": True, "args": ["--mute-audio"]}
        if CHROME.exists():
            kwargs["executable_path"] = str(CHROME)
        try:
            cls.browser = cls.pw.chromium.launch(**kwargs)
        except Exception as exc:  # no browser available
            cls.pw.stop()
            cls.srv.should_exit = True
            raise unittest.SkipTest(f"no browser: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.should_exit = True

    def walk(self, width, height):
        ctx = self.browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        ctx.add_init_script("if (window.speechSynthesis) window.speechSynthesis.speak = () => {};")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        overflow = lambda: page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")

        page.goto(f"http://127.0.0.1:{self.port}/")
        page.wait_for_selector("h1:has-text('Describe your ideal customer')")
        self.assertIn("Recorded example, 2 Sep 2026", page.inner_text("#run-pill"))
        self.assertEqual(overflow(), 0)

        page.click("#primary")  # 1 -> 2
        page.wait_for_selector("h1:has-text('Agent research')")
        page.wait_for_selector("#primary:not([disabled])")
        self.assertEqual(page.locator(".tl-item").count(), 18)
        self.assertEqual(overflow(), 0)

        page.click("#primary")  # 2 -> 3
        page.wait_for_selector("tbody tr")
        self.assertEqual(page.locator("tbody tr").count(), 2)
        self.assertEqual(overflow(), 0)

        page.click("#primary")  # open top draft
        page.wait_for_selector("#drawer.open")
        self.assertEqual(page.input_value("#d-subject"), "Hiring AI engineers at TrueFoundry")
        self.assertIn("Passed on the first attempt", page.inner_text("#drawer"))
        page.click("#approve")
        page.wait_for_selector("text=Approved · Undo")
        self.assertIn("Nothing was sent", page.inner_text("#drawer"))
        self.assertEqual(overflow(), 0)
        self.assertEqual(errors, [])
        ctx.close()

    def test_desktop_click_path(self):
        self.walk(1440, 900)

    def test_phone_click_path(self):
        self.walk(390, 844)


if __name__ == "__main__":
    unittest.main()
