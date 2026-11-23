"""Optional real Chromium acceptance test; no OBS or sample video required.

Run from the repository root:
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/browser-venv/bin/python -m tests.browser_smoke
Setup: uv venv .local/browser-venv && uv pip install --python .local/browser-venv/bin/python playwright pillow
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/browser-venv/bin/python -m playwright install chromium
"""

import base64
import os
import time
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import quote

from mkw_tracker.server import Application, make_server


class ReviewQueue:
    """Controlled capture boundary. Scoring still uses the real persistent store."""

    def __init__(self, store, candidate):
        self.store = store
        self.pending = [candidate]
        self.reviewed = None
        self.thread = None
        self.stop_event = threading.Event()
        self.reader = None
        self.client = None

    def snapshot(self):
        return {"status": "test capture", "message": "Controlled browser test", "pending": self.pending}

    def review(self, candidate_id, players):
        candidate = next(item for item in self.pending if item["id"] == candidate_id)
        result = self.store.review_result(candidate["race_id"], players)
        self.reviewed = players
        self.pending.remove(candidate)
        return result

    def prune_deleted_races(self):
        race_ids = {race["id"] for race in self.store.snapshot()["races"]}
        self.pending = [item for item in self.pending if item["race_id"] in race_ids]

    def close(self):
        pass


def main():
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError as error:
        raise SystemExit("Optional browser tests need Playwright. See tests/browser_smoke.py setup commands.") from error

    # Camera controls use fake HTTP responses, not the host's V4L2 backend.
    with tempfile.TemporaryDirectory() as temporary, patch("mkw_tracker.cameras.CAMERA_BACKEND_AVAILABLE", True):
        directory = Path(temporary)
        app = Application(directory / "scores.sqlite", directory)
        app.reader_preferences = {'default': 'qwen-4b-gguf', 'warning': None}
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                expected_http_error = [False]
                def check_console(message):
                    if message.type == "error":
                        if expected_http_error[0] and message.text.startswith("Failed to load resource:") and any(status in message.text for status in ("400", "500")):
                            return
                        errors.append(message.text)
                page.on("console", check_console)
                dialogs = []
                def accept_dialog(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept()
                page.on("dialog", accept_dialog)
                base = f"http://127.0.0.1:{server.server_address[1]}"
                page.goto(base)
                expect(page.get_by_role('link', name='License & source', exact=True)).to_be_visible()
                page.get_by_role('link', name='License & source', exact=True).click()
                expect(page.get_by_role('heading', name='License & source', exact=True)).to_be_visible()
                expect(page.locator('#project-source')).to_have_attribute(
                    'href', 'https://github.com/ebuttonsdude/mkw-mogi-tracker')
                expect(page.get_by_text('NO WARRANTY', exact=True)).to_be_visible()
                page.set_viewport_size({'width': 360, 'height': 800})
                expect(page.locator('#project-source')).to_be_visible()
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.get_by_role('link', name='Back to tracker', exact=True).click()
                expect(page.locator('#camera-settings select[name=name_engine]')).to_have_value('qwen-4b-gguf')
                expect(page.locator('#import-video select[name=name_engine]')).to_have_value('qwen-4b-gguf')
                page.locator('#camera-settings select[name=name_engine]').evaluate("(select) => { select.value = 'tesseract'; select.dispatchEvent(new Event('change')); }")
                page.evaluate('refresh()')
                expect(page.locator('#camera-settings select[name=name_engine]')).to_have_value('tesseract')
                app.reader_preferences = {'default': 'auto', 'warning': None}
                page.reload()
                expect(page.locator("#mode")).to_have_text("Local race tracker")
                page.locator("#auto-refresh").uncheck()
                expect(page.locator("#character-portrait")).to_be_hidden()
                expect(page.get_by_role("navigation", name="Dashboard sections")).to_be_visible()
                expect(page.get_by_role("button", name="Reset session scores")).to_have_count(0)
                expect(page.get_by_role("tab", name="Standings", exact=True)).to_have_attribute("aria-selected", "true")
                def tab(name):
                    page.get_by_role("tab", name=name, exact=True).click()
                    expect(page.get_by_role("tabpanel", name=name, exact=True)).to_be_visible()

                # Tabs use roving focus, keyboard navigation, and survive reloads.
                page.get_by_role("tab", name="Standings", exact=True).focus()
                page.keyboard.press("End")
                expect(page.get_by_role("tab", name="Settings", exact=True)).to_be_focused()
                page.keyboard.press("Home")
                page.keyboard.press("ArrowRight")
                expect(page.get_by_role("tab", name="Capture & review", exact=True)).to_be_focused()
                page.reload()
                expect(page.get_by_role("tab", name="Capture & review", exact=True)).to_have_attribute("aria-selected", "true")
                page.locator("#auto-refresh").uncheck()
                expect(page.locator("#capture")).to_contain_text("Choose a capture input")
                expect(page.locator("#capture-mode")).to_have_value("obs")
                expect(page.locator("#obs-source")).to_be_visible()
                expect(page.locator("#obs-source")).to_be_enabled()
                expect(page.locator("#camera-obs-fields")).to_be_visible()
                expect(page.locator("#camera-device-field")).to_be_hidden()
                expect(page.locator("#camera-refresh")).to_be_hidden()
                expect(page.locator("#camera-settings select[name=name_engine]")).to_have_value("auto")
                qwen_choices = {
                    "qwen": ("Qwen3-VL-8B BF16", "17.48"),
                    "qwen-4b-nf4": ("Qwen3-VL-4B NF4", "4.83"),
                    "qwen-4b-gguf": ("Qwen3-VL-4B GGUF Q4_K_M", None),
                    "qwen-int8": ("Qwen3-VL-8B INT8", "11.73"),
                    "qwen-nvfp4": ("Qwen3-VL-8B NVFP4", "8.74"),
                    "qwen36-nvfp4": ("Qwen3.6-27B NVFP4", "22.98"),
                }
                for form_id, prefix in (("camera-settings", "capture"), ("import-video", "import")):
                    select = page.locator(f"#{form_id} select[name=name_engine]")
                    expect(select).to_have_value("auto")
                    expect(select.locator("option")).to_have_count(len(qwen_choices) + 4)
                    expect(select).to_have_attribute("aria-describedby", f"{prefix}-name-engine-help {prefix}-nvfp4-warning")
                    for value, (label, memory) in qwen_choices.items():
                        option = select.locator(f'option[value="{value}"]')
                        expect(option).to_contain_text(label)
                        expect(option).to_contain_text(f"peak ≈{memory} GiB" if memory else "Vulkan / CPU")
                        if value == 'qwen-4b-gguf':
                            expect(option).to_contain_text('recommended')
                    expect(select.locator('option').nth(1)).to_have_attribute('value', 'qwen-4b-gguf')
                    expect(page.locator(f"#{prefix}-name-engine-help")).to_contain_text("Observed peak process VRAM")
                    expect(page.locator(f"#{prefix}-name-engine-help")).to_contain_text("not total GPU use")
                    expect(page.locator(f"#{prefix}-nvfp4-warning")).to_be_hidden()
                assert app.cameras.thread is None and app.capture.thread is None
                tab("Videos")
                expect(page.get_by_label("Sample interval in seconds (default 0.5)")).to_have_value("0.5")
                # Long model labels and hardware warnings fit both mobile forms.
                for name, form_id, prefix in (("Capture & review", "camera-settings", "capture"),
                                              ("Videos", "import-video", "import")):
                    tab(name)
                    page.locator(f"#{form_id} select[name=name_engine]").select_option("qwen-nvfp4")
                    page.set_viewport_size({"width": 390, "height": 844})
                    expect(page.locator(f"#{prefix}-nvfp4-warning")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.locator(f"#{form_id} select[name=name_engine]").select_option("auto")
                tab("Settings")
                page.locator("#mogi-png-details > summary").click()
                graph = page.get_by_label("Graph style", exact=True)
                expect(graph).to_have_value("cumulative")
                graph.select_option("relative")
                expect(page.locator("#message")).to_have_text("Mogi PNG graph saved.")
                page.reload()
                page.locator("#mogi-png-details > summary").click()
                expect(graph).to_have_value("relative")
                graph.select_option("cumulative")
                expect(page.locator("#message")).to_have_text("Mogi PNG graph saved.")
                page.locator("#mogi-png-details > summary").click()
                expect(page.locator("#character-theme option")).to_have_count(51)
                theme_names = page.locator("#character-theme option").all_text_contents()[1:]
                assert theme_names == sorted(theme_names, key=str.casefold), theme_names
                expect(page.locator("#overlay-url")).to_have_value(base + "/overlay")
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.locator("#character-theme").select_option("luigi")
                    page.get_by_role("button", name="Save theme", exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", "luigi")
                current = page.request.get(base + "/api/state").json()
                assert current["settings"]["theme"] == "luigi"
                assert current["theme"]["icon_url"] is None
                expect(page.locator("#character-portrait")).to_be_hidden()
                expect(page.locator("#theme-character-portrait")).to_be_hidden()
                expect(page.locator("#theme-icon-note")).to_contain_text("No icon selected")
                assert page.locator("#character-portrait").get_attribute("src") is None
                # Every builtin palette is usable without bundled artwork.
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.locator("#character-theme").select_option("cow")
                    page.get_by_role("button", name="Save theme", exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", "cow")
                expect(page.locator("#character-portrait")).to_be_hidden()
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.locator("#character-theme").select_option("luigi")
                    page.get_by_role("button", name="Save theme", exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", "luigi")
                tab("Overlays")
                page.locator("#overlay-settings input[name=width]").fill("640")
                page.locator("#overlay-settings input[name=title]").fill("Friday teams")
                page.locator("#overlay-settings input[name=animate]").uncheck()
                page.locator('#overlay-settings select[name=slide_direction]').select_option('right')
                page.locator('#overlay-settings input[name=slide_duration]').fill('750')
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.get_by_role("button", name="Save overlay settings", exact=True).click()
                overlay_settings = page.request.get(base + "/api/state").json()["settings"]["overlay"]
                assert overlay_settings["width"] == 640 and overlay_settings["title"] == "Friday teams" and not overlay_settings["animate"]
                assert overlay_settings['slide_direction'] == 'right' and overlay_settings['slide_duration'] == 750
                page.locator('#overlay-hide').click()
                expect(page.locator('#overlay-visibility')).to_have_text('Overlay hidden')
                page.locator('#overlay-toggle').click()
                expect(page.locator('#overlay-visibility')).to_have_text('Overlay shown')
                page.locator('#overlay-show').click()
                expect(page.locator('#overlay-show')).to_be_enabled()
                # Camera controls are exercised against a fake HTTP boundary, never hardware.
                camera_state = {"devices": [{"id": "/dev/video10", "path": "/dev/video10", "name": "OBS Virtual Camera", "available": True, "error": None}],
                                "status": {"state": "stopped", "device_id": None, "name_engine": "auto", "capture_mode": "obs", "message": "Ready"}}
                camera_requests = []
                camera_discoveries = []
                obs_failures = []
                obs_connection = {"url": "ws://127.0.0.1:4455", "password_saved": False}
                def camera_route(route):
                    operation = route.request.url.rsplit("/", 1)[-1]
                    if operation == "start":
                        camera_requests.append(route.request.post_data_json)
                        if camera_requests[-1].get("obs_password"):
                            obs_connection.update(url=camera_requests[-1]["obs_url"], password_saved=True)
                        if obs_failures:
                            route.fulfill(status=400, json={"error": obs_failures.pop()})
                            return
                        obs_mode = camera_requests[-1].get("capture_mode") == "obs"
                        camera_state["status"].update(
                            state="running", busy=True, device_id=None if obs_mode else "/dev/video10",
                            capture_mode="obs" if obs_mode else "camera",
                            obs_source=camera_requests[-1].get("obs_source"),
                            name_engine=camera_requests[-1]["name_engine"],
                            message="OBS screenshots active" if obs_mode else "Camera active",
                        )
                    elif operation == "stop":
                        camera_state["status"].update(state="stopped", busy=False, device_id=None, message="Stopped")
                    elif operation == "cameras":
                        camera_discoveries.append(route.request.url)
                    route.fulfill(json=camera_state)
                def camera_snapshot(route):
                    response = route.fetch()
                    body = response.json()
                    body["cameras"] = camera_state
                    body["obs_connection"] = obs_connection
                    if camera_state["status"]["state"] == "running":
                        selected = camera_state["status"]["name_engine"]
                        body["capture"].update(name_engine="qwen" if selected == "auto" else selected,
                                               name_engine_status="ready")
                    route.fulfill(response=response, json=body)
                page.route("**/api/cameras", camera_route)
                page.route("**/api/cameras/*", camera_route)
                page.route("**/api/state", camera_snapshot)
                tab("Capture & review")
                with page.expect_response(base + "/api/state"):
                    page.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#capture-mode")).to_have_value("obs")
                page.locator("#obs-source").fill("Default OBS source")
                page.locator("#camera-start").click()
                expect(page.locator("#camera-status")).to_contain_text("OBS screenshots active")
                assert camera_requests[-1] == {
                    "capture_mode": "obs", "obs_source": "Default OBS source", "name_engine": "auto",
                    "obs_url": "ws://127.0.0.1:4455",
                }
                assert camera_discoveries == []
                page.locator("#camera-stop").click()
                expect(page.locator("#camera-status")).to_contain_text("Stopped")
                # Selecting a model only changes the form; start/stop remain explicit.
                for engine, (label, _) in qwen_choices.items():
                    requests_before = len(camera_requests)
                    page.locator("#camera-settings select[name=name_engine]").select_option(engine)
                    warning = page.locator("#capture-nvfp4-warning")
                    if engine.endswith("nvfp4"):
                        expect(warning).to_be_visible()
                        expect(warning).to_contain_text("NVFP4 requires")
                        expect(warning).to_contain_text("not RTX 4070 or Radeon")
                        expect(warning).to_contain_text("may not have enough free VRAM")
                    else:
                        expect(warning).to_be_hidden()
                    with page.expect_response(base + "/api/state"):
                        page.get_by_role("button", name="Refresh", exact=True).click()
                    expect(page.locator("#camera-settings select[name=name_engine]")).to_have_value(engine)
                    assert len(camera_requests) == requests_before
                    page.locator("#camera-start").click()
                    expect(page.locator("#camera-status")).to_contain_text("OBS screenshots active")
                    assert camera_requests[-1]["name_engine"] == engine
                    expect(page.locator("#capture")).to_contain_text(f"Name reader: {label}")
                    page.locator("#camera-stop").click()
                    expect(page.locator("#camera-status")).to_contain_text("Stopped")
                page.locator("#camera-settings select[name=name_engine]").select_option("auto")
                expect(page.locator("#capture-nvfp4-warning")).to_be_hidden()
                page.locator("#capture-mode").select_option("camera")
                page.locator("#camera-refresh").click()
                expect(page.locator("#camera-device option")).to_have_count(2)
                expect(page.locator("#camera-errors")).to_be_hidden()
                camera_state["devices"].append({"id": "/dev/video99", "path": "/dev/video99", "name": "Unavailable test camera", "available": False, "error": "Permission denied"})
                page.locator("#camera-refresh").click()
                expect(page.locator("#camera-errors")).to_be_visible()
                expect(page.locator("#camera-errors")).to_contain_text("Permission denied")
                camera_state["devices"].pop()
                page.locator("#camera-refresh").click()
                expect(page.locator("#camera-errors")).to_be_hidden()
                page.locator("#camera-device").select_option("/dev/video10")
                page.locator("#camera-start").click()
                expect(page.locator("#camera-status")).to_contain_text("Camera active")
                expect(page.locator("#camera-device")).to_be_disabled()
                assert camera_requests[-1] == {"capture_mode": "camera", "device_id": "/dev/video10", "name_engine": "auto", "use_obs_clock": False}
                page.locator("#camera-stop").click()
                expect(page.locator("#camera-status")).to_contain_text("Stopped")
                page.locator("#camera-obs-clock").check()
                expect(page.locator("#camera-obs-fields")).to_be_visible()
                page.locator("#camera-settings input[name=obs_password]").fill("test-secret")
                obs_connection["error"] = "Could not save OBS credentials locally."
                with page.expect_response(base + "/api/state"):
                    page.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#camera-settings input[name=obs_password]")).to_have_value("test-secret")
                expect(page.locator("#obs-credentials-error")).to_be_visible()
                expect(page.locator("#obs-credentials-error")).to_contain_text("Could not save")
                obs_connection["error"] = None
                page.locator("#camera-start").click()
                expect(page.locator("#camera-status")).to_contain_text("Camera active")
                assert camera_requests[-1]["obs_password"] == "test-secret"
                expect(page.locator("#camera-settings input[name=obs_password]")).to_have_value("")
                page.locator("#camera-stop").click()
                expect(page.locator("#camera-status")).to_contain_text("Stopped")
                expect(page.locator("#obs-password-status")).to_have_text("Saved locally")
                expect(page.locator("#camera-settings input[name=obs_password]")).to_have_attribute("placeholder", "Leave blank to use saved password")
                page.locator("#camera-start").click()
                expect(page.locator("#camera-status")).to_contain_text("Camera active")
                assert "obs_password" not in camera_requests[-1]
                page.locator("#camera-stop").click()
                expect(page.locator("#camera-status")).to_contain_text("Stopped")
                # OBS capture shares credentials but never opens or discovers a camera.
                discoveries_before = len(camera_discoveries)
                page.locator("#capture-mode").select_option("obs")
                expect(page.locator("#camera-device-field")).to_be_hidden()
                expect(page.locator("#camera-refresh")).to_be_hidden()
                expect(page.locator("#camera-obs-clock-field")).to_be_hidden()
                expect(page.locator("#camera-obs-fields")).to_be_visible()
                expect(page.locator("#obs-source")).to_be_visible()
                page.locator("#obs-source").fill("Desktop")
                page.locator("#camera-settings input[name=obs_url]").fill("ws://127.0.0.1:4456")
                with page.expect_response(base + "/api/state"):
                    page.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#capture-mode")).to_have_value("obs")
                expect(page.locator("#obs-source")).to_have_value("Desktop")
                expect(page.locator("#camera-settings input[name=obs_url]")).to_have_value("ws://127.0.0.1:4456")
                page.locator("#capture-mode").select_option("camera")
                expect(page.locator("#camera-device")).to_have_value("/dev/video10")
                page.locator("#capture-mode").select_option("obs")
                expect(page.locator("#obs-source")).to_have_value("Desktop")
                expected_http_error[0] = True
                obs_failures.append("OBS source not found")
                page.locator("#camera-start").click()
                expect(page.locator("#message")).to_contain_text("OBS source not found")
                expected_http_error[0] = False
                expect(page.locator("#camera-start")).to_be_enabled()
                expect(page.locator("#obs-source")).to_have_value("Desktop")
                page.locator("#camera-start").click()
                expect(page.locator("#camera-status")).to_contain_text("OBS screenshots active")
                expect(page.locator("#capture-mode")).to_be_disabled()
                expect(page.locator("#obs-source")).to_be_disabled()
                assert camera_requests[-1] == {
                    "capture_mode": "obs", "obs_source": "Desktop", "name_engine": "auto",
                    "obs_url": "ws://127.0.0.1:4456",
                }
                camera_state["status"].update(state="error", busy=True, message="OBS reconnecting")
                with page.expect_response(base + "/api/state"):
                    page.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#camera-status")).to_contain_text("OBS reconnecting")
                for selector in ("#camera-start", "#capture-mode", "#obs-source", "#camera-device"):
                    expect(page.locator(selector)).to_be_disabled()
                expect(page.locator("#camera-stop")).to_be_enabled()
                expect(page.locator("#obs-source")).to_have_value("Desktop")
                page.locator("#camera-stop").click()
                expect(page.locator("#camera-status")).to_contain_text("Stopped")
                expect(page.locator("#obs-source")).to_be_enabled()
                expect(page.locator("#capture-mode")).to_be_enabled()
                expect(page.locator("#camera-start")).to_be_enabled()
                camera_state["status"].update(state="error", busy=False, message="OBS worker exited")
                with page.expect_response(base + "/api/state"):
                    page.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#camera-status")).to_contain_text("OBS worker exited")
                for selector in ("#camera-start", "#capture-mode", "#obs-source"):
                    expect(page.locator(selector)).to_be_enabled()
                expect(page.locator("#obs-source")).to_have_value("Desktop")
                assert len(camera_discoveries) == discoveries_before
                page.locator("#capture-mode").select_option("camera")
                expect(page.locator("#camera-device")).to_be_enabled()
                def clear_credentials(route):
                    assert route.request.method == "DELETE"
                    obs_connection["password_saved"] = False
                    route.fulfill(json=obs_connection)
                page.route("**/api/obs/credentials", clear_credentials)
                page.locator("#obs-password-clear").click()
                expect(page.locator("#obs-password-status")).to_have_text("No password saved")
                expect(page.locator("#obs-password-clear")).to_be_hidden()
                page.unroute("**/api/obs/credentials", clear_credentials)
                camera_state["devices"] = []
                page.locator("#camera-refresh").click()
                expect(page.locator("#camera-device")).to_contain_text("No camera inputs found")
                expect(page.locator("#camera-start")).to_be_disabled()
                page.locator("#capture-mode").select_option("obs")
                expect(page.locator("#camera-start")).to_be_enabled()
                page.locator("#capture-mode").select_option("camera")
                page.unroute("**/api/cameras", camera_route)
                page.unroute("**/api/cameras/*", camera_route)
                page.unroute("**/api/state", camera_snapshot)
                tab("Standings")

                def api(path, method="GET", body=None):
                    if method == "POST" and path in ("/api/players", "/api/teams", "/api/races"):
                        body = {"expected_session_id": api("/api/state")["session"]["id"], **(body or {})}
                    response = page.request.fetch(base + path, method=method, data=body)
                    assert response.ok, response.text()
                    return response.json()

                def refresh():
                    with page.expect_response(base + "/api/state"):
                        page.get_by_role("button", name="Refresh", exact=True).click()
                    expect(page.locator("#refresh")).to_be_enabled()

                def submit(form_id, endpoint):
                    with page.expect_response(lambda response: response.url.endswith(endpoint) and response.request.method == "POST") as response:
                        page.locator(f"#{form_id} button").first.click()
                    assert response.value.ok, response.value.text()
                    expect(page.locator(f"#{form_id} button").first).to_be_enabled()

                # OBS output failures stay visible independently of transient messages and recover cleanly.
                def obs_failure_state(route):
                    response = route.fetch()
                    body = response.json()
                    body["obs_text_error"] = "Permission denied writing team-standings.txt"
                    body["migration_notice"] = "Legacy sessions migrated. Review archived player deletion flags."
                    route.fulfill(response=response, json=body)
                page.route("**/api/state", obs_failure_state)
                refresh()
                expect(page.locator("#migration-notice")).to_have_count(0)
                expect(page.locator("#obs-text-error")).to_be_visible()
                expect(page.locator("#obs-text-error")).to_contain_text("OBS may show stale standings")
                expect(page.locator("#obs-text-error")).to_contain_text("Permission denied")
                page.locator("#auto-refresh").check()
                page.locator("#auto-refresh").blur()
                with page.expect_response(base + "/api/state", timeout=6000):
                    pass
                expect(page.locator("#obs-text-error")).to_be_visible()
                page.locator("#auto-refresh").uncheck()
                page.unroute("**/api/state", obs_failure_state)
                refresh()
                expect(page.locator("#obs-text-error")).to_be_hidden()
                expect(page.locator("#obs-text-error")).to_have_text("")
                expect(page.locator("#migration-notice")).to_have_count(0)

                def create_session(name):
                    tab("Capture & review")
                    page.locator("#new-session input[name=name]").fill(name)
                    submit("new-session", "/api/sessions")

                # Drafts stay bound to their displayed session, even after polling and redraw.
                original_session = api("/api/state")["session"]
                tab("Standings")
                page.locator("#add-team input").fill("Stale team draft")
                page.locator("#add-player input").fill("Stale player draft")
                tab("Race history")
                page.locator("#add-race input[name=course]").fill("Stale course draft")
                displayed_session = page.locator("#session").inner_text()
                other_session = api("/api/sessions", "POST", {"name": "Other tab session", "expected_session_id": original_session["id"]})
                page.locator("#auto-refresh").check()
                page.locator("#add-race input[name=course]").focus()
                with page.expect_response(base + "/api/state", timeout=6000):
                    pass
                page.wait_for_function("id => state.session.id === id", arg=other_session["id"])
                expect(page.locator("#session")).to_have_text(displayed_session)
                page.locator("#auto-refresh").uncheck()
                for form_id, endpoint, view, field, value in (
                    ("add-race", "/api/races", "Race history", "course", "Stale course draft"),
                    ("add-team", "/api/teams", "Standings", "name", "Stale team draft"),
                    ("add-player", "/api/players", "Standings", "name", "Stale player draft"),
                ):
                    tab(view)
                    expected_http_error[0] = True
                    with page.expect_response(lambda response: response.url.endswith(endpoint) and response.request.method == "POST") as response:
                        page.locator(f"#{form_id} button").first.click()
                    assert response.value.status == 400, response.value.text()
                    expect(page.locator("#message")).to_contain_text("session changed")
                    expect(page.locator(f"#{form_id} input[name={field}]")).to_have_value(value)
                    expected_http_error[0] = False
                    refresh()  # Even an explicit redraw must not silently retarget a draft.
                assert api("/api/state")["players"] == []
                assert api("/api/state")["teams"] == []
                assert api("/api/state")["races"] == []
                page.reload()  # Restart empty drafts against the now-visible session.
                expect(page.locator("#mode")).to_have_text("Local race tracker")
                page.locator("#auto-refresh").uncheck()
                tab("Standings")
                page.locator("#add-player input").fill("Fresh context")
                submit("add-player", "/api/players")
                current = api("/api/state")["session"]
                api("/api/folders/" + original_session["folder_id"] + "/load", "POST", {"expected_session_id": current["id"]})
                api("/api/folders/" + current["folder_id"], "DELETE", {})
                refresh()

                tab("Standings")
                page.locator("#add-team input").fill("Our team")
                submit("add-team", "/api/teams")
                page.locator("#add-team input").fill("Room")
                submit("add-team", "/api/teams")
                page.locator("#add-player input").fill("プレイヤー 1")
                submit("add-player", "/api/players")
                for index in range(2, 13):
                    api("/api/players", "POST", {"name": f"Player {index}"})
                refresh()
                snapshot = api("/api/state")
                players = snapshot["players"]
                teams = snapshot["teams"]
                ids = [player["id"] for player in players]
                assert len(ids) == 12
                for player, team in zip(players, [teams[0]] * 6 + [teams[1]] * 6):
                    api(f"/api/players/{player['id']}", "PATCH", {"team_id": team["id"]})
                refresh()

                team_color = page.locator('#teams input[type=color]').first
                team_color.fill('#12abef')
                with page.expect_response(lambda response: '/api/teams/' in response.url and response.request.method == 'PATCH'):
                    team_color.dispatch_event('change')
                expect(page.locator('#teams input[type=color]').first).to_have_value('#12abef')
                assert any(team["color"] == "#12abef" for team in api("/api/state")["teams"])
                tab("Overlays")
                page.locator('#overlay-highlight-team').select_option(teams[0]['id'])
                page.locator('#overlay-settings input[name=alternating_backgrounds]').check()
                with page.expect_response(lambda response: response.url.endswith('/api/settings') and response.request.method == 'PATCH'):
                    page.get_by_role('button', name='Save overlay settings', exact=True).click()
                page.reload()
                expect(page.locator('#overlay-highlight-team')).to_have_value(teams[0]['id'])
                assert api('/api/state')['teams'][0]['color'] == '#12abef'
                tab("Timestamps")
                page.locator("#add-recording input").fill("Browser recording")
                submit("add-recording", "/api/recordings")
                recording = api("/api/state")["recordings"][0]
                expect(page.locator("#export-recording option").nth(1)).to_have_text("Browser recording")
                tab("Race history")
                page.locator("#add-race input[name=course]").fill("Rainbow Road")
                page.locator("#race-recording").select_option(recording["id"])
                page.locator("#add-race input[name=offset]").fill("00:01:23.456")
                submit("add-race", "/api/races")
                expect(page.locator("#editor-panel")).to_be_visible()
                expect(page.locator("#editor-evidence")).to_contain_text("No evidence available")
                for index, player_id in enumerate(ids):
                    page.locator("#placements select").nth(index).select_option(player_id)
                submit("results", "/results")
                expect(page.locator("#editor-panel")).to_be_hidden()
                snapshot = api("/api/state")
                assert sum(player["points"] for player in snapshot["players"]) == 82
                assert [team["points"] for team in snapshot["teams"]] == [61, 21]
                tab("Standings")
                expect(page.locator("#teams")).to_contain_text("61")
                expect(page.locator("#logged-races")).to_have_text("Races logged: 1 · Results confirmed: 1 / 12")
                expect(page.locator("#player-count")).to_have_text("Recorded players: 12")
                expect(page.locator(".player-number").first).to_have_text("1")
                expect(page.locator(".player-number").last).to_have_text("12")
                expect(page.locator("#teams .team-gap").nth(1)).to_have_text("40 points")
                displayed_teams = api("/api/state")["display_standings"]["teams"]
                assert page.locator("#teams tbody tr").first.evaluate("row => row.style.getPropertyValue('--team-color')") == displayed_teams[0]["color"]
                assert page.locator("#players tbody tr").first.evaluate("row => row.style.getPropertyValue('--team-color')") == displayed_teams[0]["color"]
                expect(page.locator("#room-points")).to_have_text("Room points: 82 / 984")
                tab("Race history")
                expect(page.locator("img.course-icon")).to_have_count(0)

                # Penalties change net points without changing finishing order and can be removed.
                page.locator("#history").get_by_role("button", name="Penalties", exact=True).click()
                expect(page.locator("#penalty-player option")).to_have_count(13)
                page.locator("#penalty-player").select_option(ids[0])
                page.locator("#add-penalty input[name=points]").fill("5")
                page.locator("#add-penalty input[name=reason]").fill("Browser penalty")
                submit("add-penalty", "/penalties")
                expect(page.locator("#race-penalties")).to_contain_text("−5 points")
                expect(page.locator("#history")).to_contain_text("−5 penalty points")
                penalized_player = next(player for player in api("/api/state")["players"] if player["id"] == ids[0])
                assert penalized_player["points"] == 10 and penalized_player["penalty_points"] == 5
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    page.get_by_role("button", name="Remove penalty").click()
                expect(page.locator("#race-penalties")).to_contain_text("No penalties")
                page.get_by_role("button", name="Close penalties").click()
                tab("Overlays")
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.locator("#standings-format").select_option("md")
                expect(page.locator("#obs-download")).to_have_attribute("href", "/api/standings.md")
                expect(page.locator("#message")).to_contain_text("Update the OBS text source file path")
                expect(page.locator("#obs-text-path")).to_contain_text(".md")
                tab("Overlays")
                with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH"):
                    page.locator("#standings-format").select_option("txt")
                expect(page.locator("#obs-download")).to_have_attribute("href", "/api/standings.txt")
                tab("Race history")
                page.locator("#history").get_by_role("button", name="View evidence").click()
                expect(page.locator("#history-evidence")).to_contain_text("No evidence available")
                page.get_by_role("button", name="Close evidence").click()

                tab("Standings")
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    page.get_by_label("Team for プレイヤー 1", exact=True).select_option(teams[1]["id"])
                expect(page.locator("#teams")).to_contain_text("36")
                assert [team["points"] for team in api("/api/state")["teams"]] == [46, 36]

                tab("Race history")
                page.get_by_role("button", name="Review / correct").click()
                assert page.locator("#placements select").first.evaluate("select => [...select.options].slice(1).map(option => option.text).every((name, i, names) => i === 0 || names[i - 1].localeCompare(name) <= 0)")
                page.locator("#placements select").nth(0).select_option(ids[1])
                page.locator("#placements select").nth(1).select_option(ids[0])
                submit("results", "/results")
                snapshot = api("/api/state")
                assert snapshot["races"][0]["revision"] == 2
                totals = {player["id"]: player["points"] for player in snapshot["players"]}
                assert totals[ids[0]] == 12 and totals[ids[1]] == 15
                assert sum(totals.values()) == 82

                page.get_by_role("button", name="Edit course / chapter", exact=True).click()
                page.locator("#race-timing input[name=course]").fill("Corrected chapter")
                page.locator("#race-timing input[name=offset]").fill("91.007")
                with page.expect_response(lambda response: response.request.method == "PATCH") as response:
                    page.get_by_role("button", name="Save chapter", exact=True).click()
                assert response.value.ok
                expect(page.locator("#timing-panel")).to_be_hidden()
                tab("Timestamps")
                page.locator("#export-recording").select_option(recording["id"])
                expect(page.locator("#export-recording option:checked")).to_have_text(snapshot["folders"][0]["name"])
                expect(page.locator("#export-format")).to_have_value("txt")
                expect(page.locator("#resolve-help")).to_be_hidden()
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download Timestamps").click()
                assert Path(download.value.path()).read_text(encoding="utf-8") == "00:01:31.007 Corrected chapter\n"

                page.locator("#export-format").select_option("edl")
                expect(page.locator("#resolve-help")).to_contain_text("Timeline Markers from EDL")
                expect(page.locator("#recording-fps")).to_have_text("Reported recording rate: unknown.")
                with app.lock:
                    app.store.set_recording_fps(recording["id"], "60000/1001")
                refresh()
                expect(page.locator("#recording-fps")).to_contain_text("60000/1001 fps (about 59.94 fps)")
                # Showing source FPS or refreshing chapter data must not request an EDL.
                assert not page.evaluate("""performance.getEntriesByType('resource').some(entry =>
                    entry.name.includes('/api/chapters?') && entry.name.includes('format=edl'))""")
                with page.expect_download() as edl:
                    page.get_by_role("button", name="Download Timestamps").click()
                assert edl.value.suggested_filename == "race-chapters.edl"
                markers = Path(edl.value.path()).read_bytes()
                assert b"01:01:31:00 01:01:31:01" in markers
                assert b"|M:Corrected chapter |D:1" in markers

                first_folder_id = snapshot["session"]["folder_id"]
                first_race_id = snapshot["races"][0]["id"]
                create_session("Second session")
                tab("Race history")
                expect(page.locator("#history")).to_contain_text("Archived session")
                snapshot = api("/api/state")
                assert len(snapshot["races"]) == 1 and len(snapshot["players"]) == 0
                assert snapshot["teams"] == []
                assert len(snapshot["folders"]) == 2
                assert all(player["points"] == 0 for player in snapshot["players"])
                assert all(team["points"] == 0 for team in snapshot["teams"])

                # Archived results stay editable without repopulating the current roster.
                tab("Race history")
                page.get_by_role("button", name="Review / correct").click()
                assert page.locator("#placements select").first.locator("option").count() == 13
                submit("results", "/results")
                assert api("/api/state")["players"] == []
                page.locator("#add-folder input").fill("Practice folder")
                submit("add-folder", "/api/sessions")
                folder = next(folder for folder in api("/api/state")["folders"] if folder["id"] == first_folder_id)
                expect(page.locator(f".history-folder[data-folder-id='{folder['id']}']")).to_contain_text("Corrected chapter")
                page.locator("#history-folder").select_option(folder["id"])
                expect(page.locator(".history-folder")).to_have_count(1)
                page.locator("#history-sort").select_option("course")
                page.locator("#history-direction").select_option("asc")
                refresh()
                expect(page.locator("#history-folder")).to_have_value(folder["id"])
                expect(page.locator("#history-sort")).to_have_value("course")
                tab("Race history")
                page.locator("#history-folder").select_option("")
                page.remove_listener("dialog", accept_dialog)
                page.once("dialog", lambda dialog: dialog.accept("Renamed folder"))
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    page.locator(f".history-folder[data-folder-id='{folder['id']}']").get_by_role("button", name="Rename folder").click()
                expect(page.locator(f".history-folder[data-folder-id='{folder['id']}']")).to_contain_text("Renamed folder")
                page.on("dialog", accept_dialog)

                # Folder disclosure state survives both rerender and browser reload.
                folder_view = page.locator(f".history-folder[data-folder-id='{folder['id']}']")
                folder_view.locator("summary").click()
                expect(folder_view).not_to_have_attribute("open", "")
                refresh()
                expect(folder_view).not_to_have_attribute("open", "")
                page.reload()
                expect(folder_view).not_to_have_attribute("open", "")
                folder_view.locator("summary").click()
                folder_view.get_by_label("Lounge ID for Renamed folder").fill("1146183")
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    folder_view.get_by_role("button", name="Save Lounge ID").click()
                expect(folder_view.get_by_role("link", name="Lounge 1146183")).to_have_attribute("href", "https://lounge.mkcentral.com/mkworld/TableDetails/1146183")
                report_response = page.request.get(base + f"/api/folders/{folder['id']}/report.png")
                assert report_response.ok, report_response.text()
                # Inline preview supports copying without a download, native focus trapping and Escape.
                preview_button = folder_view.get_by_role("button", name="View folder PNG")
                preview = page.get_by_role("dialog", name="Folder PNG · Renamed folder")
                preview_downloads = []
                preview_route = f"**/api/folders/{folder['id']}/preview.png?*"
                preview_bodies = []
                def capture_preview(route):
                    response = route.fetch()
                    preview_bodies.append(response.body())
                    route.fulfill(response=response)
                page.route(preview_route, capture_preview)
                page.evaluate('''() => {
                    window.revokedPngUrls = []; window.originalRevokeUrl = URL.revokeObjectURL;
                    URL.revokeObjectURL = url => { window.revokedPngUrls.push(url); window.originalRevokeUrl(url); };
                }''')
                def preview_download(download):
                    preview_downloads.append(download)
                page.on("download", preview_download)
                # Older Chromium blurs disabled buttons before showModal records the opener.
                preview_button.evaluate('''button => {
                    const dialog = document.querySelector('#folder-png-preview');
                    const showModal = dialog.showModal;
                    dialog.showModal = function () {
                        window.previewOpenerFocused = document.activeElement === button && !button.disabled;
                        dialog.showModal = showModal;
                        return showModal.call(this);
                    };
                }''')
                with page.expect_response(lambda response: response.url.startswith(base + f"/api/folders/{folder['id']}/preview.png?")):
                    preview_button.click()
                assert page.evaluate("window.previewOpenerFocused"), "Keep the opener focusable when opening its dialog"
                expect(preview).to_be_visible()
                expect(preview.get_by_role("button", name="Close preview")).to_be_focused()
                expect(preview.locator("#folder-png-image")).to_be_visible()
                page.wait_for_function('() => document.querySelector("#folder-png-image").naturalWidth > 0')
                preview_url = preview.locator("#folder-png-image").get_attribute("src")
                assert preview_url.startswith("blob:"), preview_url
                expect(preview.get_by_role("link", name="Open full-size PNG")).to_have_attribute("href", preview_url)
                preview_evidence = Path(".omx/evidence/overlay-movement-preview")
                preview_evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(preview_evidence / "folder-preview.png"), full_page=True)
                shown_png = list(preview_bodies[0])
                original_theme = api("/api/state")["settings"]["theme"]
                page.request.patch(base + "/api/settings", data={"theme": "bowser"})
                # Mock the clipboard boundary; never modify the user's actual clipboard.
                page.evaluate('''() => {
                    window.originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
                    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
                }''')
                preview.get_by_role("button", name="Copy image").click()
                expect(preview.locator("#folder-png-status")).to_contain_text("Right-click the image and choose Copy image")
                page.evaluate('''() => Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                    write: async items => {
                        const blob = await items[0].getType('image/png');
                        window.copiedPngBytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
                    }
                }})''')
                preview.get_by_role("button", name="Copy image").click()
                expect(preview.locator("#folder-png-status")).to_have_text("PNG copied. Paste it into an app that accepts images.")
                copied_png = page.evaluate("window.copiedPngBytes")
                assert copied_png == shown_png
                assert len(preview_bodies) == 1, "Copy must reuse the displayed PNG, not request new results"
                page.evaluate('''() => Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                    write: async () => { throw new DOMException('Denied', 'NotAllowedError'); }
                }})''')
                preview.get_by_role("button", name="Copy image").click()
                expect(preview.locator("#folder-png-status")).to_contain_text("Could not copy the image. Right-click")
                expect(preview.get_by_role("button", name="Copy image")).to_be_enabled()
                page.keyboard.press("Escape")
                expect(preview).not_to_be_visible()
                expect(preview_button).to_be_focused()
                page.wait_for_function("url => window.revokedPngUrls.includes(url)", arg=preview_url)
                with page.expect_response(lambda response: response.url.startswith(base + f"/api/folders/{folder['id']}/preview.png?")):
                    preview_button.click()
                expect(preview.locator("#folder-png-image")).to_be_visible()
                reopened_png = list(preview_bodies[1])
                assert reopened_png != shown_png, "Reopening should show the newly selected theme"
                report_state = api('/api/state')
                report_teams = [team for team in report_state['teams'] + report_state['archived_teams']
                                if team['session_id'] == folder['session_id']]
                expect(preview.locator('#folder-png-team option')).to_have_count(len(report_teams) + 1)
                assert report_teams
                old_highlight = report_state['settings']['overlay']['highlight_team_id']
                with page.expect_response(lambda response: 'preview.png?highlight_team_id=' + report_teams[0]['id'] in response.url):
                    preview.locator('#folder-png-team').select_option(report_teams[0]['id'])
                expect(preview.locator('#folder-png-image')).to_be_visible()
                selected_url = preview.locator('#folder-png-image').get_attribute('src')
                page.wait_for_function('url => window.revokedPngUrls.includes(url)', arg=preview_url)
                with page.expect_response(lambda response: response.url.endswith('/preview.png?highlight_team_id=')):
                    preview.locator('#folder-png-team').select_option('')
                expect(preview.locator('#folder-png-image')).to_be_visible()
                page.wait_for_function('url => window.revokedPngUrls.includes(url)', arg=selected_url)
                assert api('/api/state')['settings']['overlay']['highlight_team_id'] == old_highlight
                page.set_viewport_size({"width": 390, "height": 844})
                assert preview.evaluate("node => node.getBoundingClientRect().width <= innerWidth")
                assert preview.evaluate("node => node.scrollWidth <= node.clientWidth")
                preview.get_by_role("button", name="Close preview").click()
                expect(preview).not_to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.request.patch(base + "/api/settings", data={"theme": original_theme})
                page.evaluate('''() => {
                    if (window.originalClipboard) Object.defineProperty(navigator, 'clipboard', window.originalClipboard);
                    else delete navigator.clipboard;
                }''')
                assert preview_downloads == []
                page.remove_listener("download", preview_download)
                page.unroute(preview_route, capture_preview)
                # Closing while the fetch is pending must discard its late response.
                held_previews = []
                def hold_preview(route):
                    held_previews.append(route)
                page.route(preview_route, hold_preview)
                preview_button.click()
                expect(preview.locator("#folder-png-status")).to_have_text("Loading folder PNG…")
                expect(preview.get_by_role("button", name="Copy image")).to_be_disabled()
                page.keyboard.press("Escape")
                expect(preview).not_to_be_visible()
                expect(preview_button).to_be_focused()
                held_previews[0].fulfill(status=200, content_type="image/png", body=bytes(shown_png))
                page.unroute(preview_route, hold_preview)
                preview_button.click()
                expect(preview.locator("#folder-png-image")).to_be_visible()
                expect(preview.locator("#folder-png-status")).to_have_text("Copy the image, or right-click it and choose Copy image.")
                latest_url = preview.locator("#folder-png-image").get_attribute("src")
                page.keyboard.press("Escape")
                page.wait_for_function("url => window.revokedPngUrls.includes(url)", arg=latest_url)
                page.evaluate("URL.revokeObjectURL = window.originalRevokeUrl")
                def reject_preview(route):
                    route.fulfill(status=400, json={"error": "Preview report cannot be rendered."})
                page.route(preview_route, reject_preview)
                expected_http_error[0] = True
                preview_button.click()
                expect(preview.locator("#folder-png-status")).to_have_text("Preview report cannot be rendered.")
                expect(preview.locator("#folder-png-image")).to_be_hidden()
                expect(preview.get_by_role("button", name="Copy image")).to_be_disabled()
                expect(preview.get_by_role("link", name="Open full-size PNG")).to_be_hidden()
                page.keyboard.press("Escape")
                page.unroute(preview_route, reject_preview)
                expected_http_error[0] = False
                # An export rejection must show its API error rather than download JSON with a .png suffix.
                report_route = f"**/api/folders/{folder['id']}/report.png"
                def reject_report(route):
                    route.fulfill(status=400, json={"error": "Report is too large to export."})
                failed_downloads = []
                def record_download(download):
                    failed_downloads.append(download)
                page.on("download", record_download)
                page.route(report_route, reject_report)
                expected_http_error[0] = True
                with page.expect_response(lambda response: response.url.endswith("/report.png") and response.status == 400):
                    folder_view.get_by_role("button", name="Download folder PNG").click()
                expect(page.locator("#message")).to_have_text("Report is too large to export.")
                expect(folder_view.get_by_role("button", name="Download folder PNG")).to_be_enabled()
                assert failed_downloads == []
                expected_http_error[0] = False
                page.unroute(report_route, reject_report)
                page.remove_listener("download", record_download)
                with page.expect_download() as report_download:
                    folder_view.get_by_role("button", name="Download folder PNG").click()
                assert Path(report_download.value.path()).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                folder_ids = page.locator(".history-folder").evaluate_all("nodes => nodes.map(node => node.dataset.folderId)")
                expected_folders = sorted(api("/api/state")["folders"], key=lambda item: (item["created_at"], str(item["id"])), reverse=True)
                assert folder_ids == [str(item["id"]) for item in expected_folders]

                # Delete controls remove current identities while preserving history.
                tab("Standings")
                page.locator("#add-team input").fill("Typo team")
                submit("add-team", "/api/teams")
                page.remove_listener("dialog", accept_dialog)
                page.once("dialog", lambda dialog: dialog.accept("Corrected team"))
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    page.locator("#teams").get_by_role("button", name="Rename", exact=True).click()
                expect(page.locator("#teams")).to_contain_text("Corrected team")
                page.on("dialog", accept_dialog)
                page.locator("#add-player input").fill("Typo player")
                submit("add-player", "/api/players")
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    page.get_by_role("button", name="Delete team", exact=True).click()
                expect(page.locator("#teams")).to_contain_text("No teams yet")
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    page.get_by_role("button", name="Delete player", exact=True).click()
                expect(page.locator("#players")).to_contain_text("Add the 12 players")
                assert len(api("/api/state")["races"]) == 1

                # Folders restore their own roster and scores; deleting a separate session leaves these intact.
                current_folder_id = api("/api/state")["session"]["folder_id"]
                tab("Race history")
                page.locator("#history-folder").select_option("")
                with page.expect_response(lambda response: response.url.endswith("/load")):
                    page.locator(f".history-folder[data-folder-id='{first_folder_id}']").get_by_role("button", name="Load session").click()
                expect(page.locator("#players tbody tr")).to_have_count(12)
                expect(page.locator("#teams tbody tr")).to_have_count(2)
                with page.expect_response(lambda response: response.url.endswith("/load")):
                    page.locator(f".history-folder[data-folder-id='{current_folder_id}']").get_by_role("button", name="Load session").click()
                expect(page.locator("#players")).to_contain_text("Add the 12 players")
                create_session("Disposable session")
                disposable_folder = api("/api/state")["session"]["folder_id"]
                tab("Race history")
                with page.expect_response(lambda response: response.url.endswith("/load")):
                    page.locator(f".history-folder[data-folder-id='{current_folder_id}']").get_by_role("button", name="Load session").click()
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    page.locator(f".history-folder[data-folder-id='{disposable_folder}']").get_by_role("button", name="Delete folder", exact=True).click()
                expect(page.locator(f".history-folder[data-folder-id='{disposable_folder}']")).to_have_count(0)

                # Queue controlled OCR output and exercise all 12 identity fields.
                with app.lock:
                    race = app.store.create_race(str(uuid4()), "Mario Circuit")
                    candidate_id = str(uuid4())
                    evidence = directory / "evidence"
                    evidence.mkdir(exist_ok=True)
                    (evidence / f"{candidate_id}.png").write_bytes(base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="))
                    app.store.add_evidence(race["id"], f"{candidate_id}.png", kind="suspected_results",
                                           label="Suspected results; finishing order unverified")
                    app.capture.sample_timing = {"acquisition_ms": 150, "recognition_ms": 240,
                                                 "cycle_ms": 390, "requested_interval_ms": 250}
                    candidate = {"id": candidate_id, "race_id": race["id"], "course": race["course"],
                                 "rows": [{"position": index + 1, "name": player["name"], "confidence": 90}
                                          for index, player in enumerate(players)],
                                 "reasons": ["Controlled recognition output for browser acceptance"],
                                 "screenshot_url": f"/evidence/{candidate_id}.png"}
                refresh()
                tab("Capture & review")
                expect(page.locator("#current-capture-race")).to_be_visible()
                expect(page.locator("#current-capture-race")).to_contain_text("Mario Circuit")
                expect(page.locator("#current-capture-race")).to_contain_text("Waiting for results")
                expect(page.locator("#current-capture-race")).to_contain_text("Suspected results only")
                expect(page.locator("#capture")).to_contain_text("capture 150 ms, recognition 240 ms, cycle ≈390 ms")
                expect(page.locator("#capture")).to_contain_text("Target interval: 250 ms")
                expect(page.locator("#review-count")).to_have_text("0 waiting")
                tab("Race history")
                expect(page.locator(f"tr[data-race-id='{race['id']}']")).to_contain_text("Suspected screenshot available")
                page.locator(f"tr[data-race-id='{race['id']}']").get_by_role("button", name="Enter results", exact=True).click()
                expect(page.locator("#editor-evidence")).to_contain_text("reordered cumulative standings")
                expect(page.locator("#placements select").first).to_have_value("")
                page.locator("#cancel-edit").click()
                tab("Capture & review")
                expect(page.locator("#current-capture-race img.course-icon")).to_have_count(0)
                expect(page.locator("#current-capture-race .capture-screenshot")).to_be_visible()
                pending_points = api("/api/state")["room"]["points"]
                page.locator("#current-capture-race").get_by_role("button", name="Edit course / chapter").click()
                expect(page.locator("#timing-panel")).to_be_visible()
                page.locator("#cancel-timing").click()
                tab("Capture & review")
                with app.lock:
                    app.store.add_evidence(race["id"], f"{candidate_id}.png", label="Controlled captured results")
                    app.capture = ReviewQueue(app.store, candidate)
                refresh()
                expect(page.locator("#current-capture-race")).to_be_hidden()
                assert api("/api/state")["room"]["points"] == pending_points
                expect(page.locator("#review-count")).to_have_text("1 waiting")
                expect(page.locator(".capture-placement")).to_have_count(12)
                expect(page.locator("#capture-reviews")).not_to_contain_text("First roster:")
                assert api("/api/state")["teams"] == []
                # Old archived names are not silently reused in a new session.
                for index in range(12):
                    expect(page.locator(".capture-placement select").nth(index)).to_have_value("")
                tab("Capture & review")
                screenshot = page.locator("#capture-reviews .capture-screenshot")
                expect(screenshot).to_be_visible()
                page.wait_for_function("image => image.complete && image.naturalWidth > 0", arg=screenshot.element_handle())
                evidence_path = Path(".omx/evidence")
                evidence_path.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence_path / "dashboard-capture.png"), full_page=True)
                # Both drafts exist before enrollment; the second must update after the first is confirmed.
                with app.lock:
                    next_race = app.store.create_race(str(uuid4()), "Next queued race")
                    next_candidate = {**candidate, "id": str(uuid4()), "race_id": next_race["id"], "course": next_race["course"]}
                    app.capture.pending.append(next_candidate)
                refresh()
                expect(page.locator("#review-count")).to_have_text("2 waiting")
                with page.expect_response(base + "/api/capture/review") as response:
                    page.get_by_role("button", name="Confirm identities and count points").first.click()
                assert response.value.ok, response.value.text()
                expect(page.locator("#review-count")).to_have_text("1 waiting")
                assert app.capture.reviewed == [{"name": player["name"]} for player in players]
                assert api("/api/state")["teams"] == [], "Deleted manual setup must not be regrouped"
                enrolled = {player["name"]: player["id"] for player in api("/api/state")["players"]}
                for index, player in enumerate(players):
                    expect(page.locator(".capture-placement select").nth(index)).to_have_value(str(enrolled[player["name"]]))
                with app.lock:
                    app.capture.pending.remove(next_candidate)
                refresh()
                expect(page.locator("#review-count")).to_have_text("0 waiting")
                page.reload()
                expect(page.locator("#players tbody tr")).to_have_count(12)
                tab("Race history")
                captured_row = page.locator(f"#history tr[data-race-id='{race['id']}']")
                captured_row.get_by_role("button", name="Review / correct").click()
                expect(page.locator("#editor-evidence img")).to_have_count(2)
                expect(page.locator("#editor-evidence img").first).to_be_visible()
                expect(page.locator("#editor-evidence img").last).to_have_attribute("alt", "Controlled captured results for Mario Circuit")
                expect(page.locator("#editor-evidence")).to_contain_text("Finishing order is unverified")
                page.get_by_role("button", name="Close editor", exact=True).click()
                assert len(api("/api/state")["folders"]) == 3
                assert sum(player["points"] for player in api("/api/state")["players"]) == 82

                # Delete a populated nonselected session without changing the selected roster or races.
                selected_before = api("/api/state")
                selected_folder = selected_before["session"]["folder_id"]
                create_session("Populated disposable session")
                populated_folder = api("/api/state")["session"]["folder_id"]
                disposable_team = api("/api/teams", "POST", {"name": "Disposable team"})
                disposable_players = [api("/api/players", "POST", {"name": f"Disposable {index}", "team_id": disposable_team["id"]}) for index in range(12)]
                for disposable_player in disposable_players:
                    api(f"/api/players/{disposable_player['id']}", "PATCH", {"team_id": disposable_team["id"]})
                disposable_race = api("/api/races", "POST", {"course": "Acorn Heights"})
                api(f"/api/races/{disposable_race['id']}/results", "POST", {"player_ids": [player["id"] for player in disposable_players]})
                tab("Race history")
                with page.expect_response(lambda response: response.url.endswith("/load")):
                    page.locator(f".history-folder[data-folder-id='{selected_folder}']").get_by_role("button", name="Load session").click()
                selected_before_delete = api("/api/state")
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    page.locator(f".history-folder[data-folder-id='{populated_folder}']").get_by_role("button", name="Delete folder", exact=True).click()
                selected_after_delete = api("/api/state")
                assert selected_after_delete["session"] == selected_before_delete["session"]
                assert selected_after_delete["players"] == selected_before_delete["players"]
                assert selected_after_delete["teams"] == selected_before_delete["teams"]
                assert [item for item in selected_after_delete["races"] if item["session_id"] == selected_after_delete["session"]["id"]] == [item for item in selected_before_delete["races"] if item["session_id"] == selected_before_delete["session"]["id"]]
                assert disposable_race["id"] not in {item["id"] for item in selected_after_delete["races"]}

                # Individual race deletion removes scores/penalties and closes every editor for that race.
                deletion_before = api("/api/state")
                delete_race = api("/api/races", "POST", {"course": "Delete only this race"})
                api(f"/api/races/{delete_race['id']}/results", "POST", {"player_ids": [player["id"] for player in deletion_before["players"]]})
                api(f"/api/races/{delete_race['id']}/penalties", "POST", {"player_id": deletion_before["players"][0]["id"], "points": 5, "reason": "Delete with race"})
                refresh()
                delete_row = page.locator(f"#history tr[data-race-id='{delete_race['id']}']")
                delete_row.get_by_role("button", name="Review / correct").click()
                delete_row.get_by_role("button", name="Edit course / chapter").click()
                delete_row.get_by_role("button", name="Penalties", exact=True).click()
                delete_row.get_by_role("button", name="View evidence").click()
                for panel in ("editor-panel", "timing-panel", "penalty-panel", "evidence-panel"):
                    expect(page.locator(f"#{panel}")).to_be_visible()
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    delete_row.get_by_role("button", name="Delete race", exact=True).click()
                assert "Delete only this race" in dialogs[-1] and "results and penalties" in dialogs[-1]
                for panel in ("editor-panel", "timing-panel", "penalty-panel", "evidence-panel"):
                    expect(page.locator(f"#{panel}")).to_be_hidden()
                deletion_after = api("/api/state")
                assert deletion_after["players"] == deletion_before["players"]
                assert deletion_after["room"] == deletion_before["room"]
                assert delete_race["id"] not in {item["id"] for item in deletion_after["races"]}

                # Cleanup touches only temporary app-upload copies, never local-path originals or race data.
                uploads_directory = directory / "uploads"
                uploads_directory.mkdir(exist_ok=True)
                old_video = uploads_directory / f"{uuid4()}-old-test.mkv"
                new_video = uploads_directory / f"{uuid4()}-new-test.mkv"
                old_video.write_bytes(b"old uploaded video")
                new_video.write_bytes(b"new uploaded video")
                old_time = time.time() - 45 * 86400
                os.utime(old_video, (old_time, old_time))
                external_video = directory / "external-original.mkv"
                external_video.write_bytes(b"external original")
                cleanup_before = api("/api/state")
                refresh()
                tab("Videos")
                expect(page.locator("#video-library tbody tr")).to_have_count(2)
                expect(page.locator("#video-library")).not_to_contain_text("external-original")
                video_file = next(item for item in cleanup_before["videos"]["files"] if item["name"].endswith("new-test.mkv"))
                video_row = page.locator("#video-library tbody tr").filter(has_text="new-test.mkv")
                delete_video_route = f"**/api/videos/{quote(str(video_file['id']), safe='')}"
                def reject_video_delete(route):
                    route.fulfill(status=500, json={"error": "Could not remove uploaded video."})
                page.route(delete_video_route, reject_video_delete)
                expected_http_error[0] = True
                with page.expect_response(lambda response: response.status == 500):
                    video_row.get_by_role("button", name="Delete video", exact=True).click()
                expect(page.locator("#message")).to_have_text("Could not remove uploaded video.")
                expect(video_row.get_by_role("button", name="Delete video", exact=True)).to_be_enabled()
                assert new_video.exists()
                expected_http_error[0] = False
                page.unroute(delete_video_route, reject_video_delete)
                with page.expect_response(lambda response: response.request.method == "DELETE"):
                    video_row.get_by_role("button", name="Delete video", exact=True).click()
                expect(page.locator("#video-library tbody tr")).to_have_count(1)
                assert "new-test.mkv" in dialogs[-1] and "18 bytes" in dialogs[-1]
                assert not new_video.exists() and old_video.exists()
                # A partial cleanup reports all counts and filename/error details, then refreshes remaining files.
                failed_video = uploads_directory / f"{uuid4()}-locked-test.mkv"
                failed_video.write_bytes(b"remaining uploaded video")
                os.utime(failed_video, (old_time, old_time))
                refresh()
                def partial_cleanup(route):
                    old_video.unlink()
                    route.fulfill(json={"deleted_files": 1, "deleted_bytes": 18, "skipped": 2,
                                        "errors": [{"name": "locked-test.mkv <unsafe>", "error": "Permission denied <details>"}]})
                page.route("**/api/videos/cleanup", partial_cleanup)
                submit("cleanup-videos", "/api/videos/cleanup")
                expect(page.locator("#message")).to_have_class("error")
                expect(page.locator("#message")).to_contain_text("Deleted 1 uploaded video (18 bytes). Skipped 2. Failed 1.")
                expect(page.locator("#message")).to_contain_text("locked-test.mkv <unsafe>: Permission denied <details>")
                expect(page.locator("#message").locator("*")).to_have_count(0)
                expect(page.locator("#video-library tbody tr")).to_have_count(1)
                expect(page.locator("#video-library")).to_contain_text("locked-test.mkv")
                assert not old_video.exists() and failed_video.exists()
                page.unroute("**/api/videos/cleanup", partial_cleanup)
                submit("cleanup-videos", "/api/videos/cleanup")
                assert "older than 30 days" in dialogs[-1] and "1 eligible files" in dialogs[-1]
                expect(page.locator("#message")).not_to_have_class("error")
                assert not failed_video.exists()
                expect(page.locator("#video-library")).to_contain_text("No app-managed uploaded videos")
                assert not old_video.exists() and external_video.read_bytes() == b"external original"
                assert (evidence / f"{candidate_id}.png").exists()
                cleanup_after = api("/api/state")
                for key in ("session", "players", "teams", "races", "room"):
                    assert cleanup_after[key] == cleanup_before[key], f"Video deletion changed {key}"

                refresh()
                tab("Race history")
                page.locator("#history-folder").select_option(api("/api/state")["session"]["folder_id"])
                # History order is independent of the temporary column sorts.
                page.locator("#history-sort").select_option("history_order")
                page.locator("#history-direction").select_option("desc")
                rows = page.locator("#history tbody tr")
                first_id, second_id = rows.evaluate_all("rs => rs.slice(0, 2).map(r => r.dataset.raceId)")
                with page.expect_response(lambda r: r.url.endswith("/race-order")) as ordered:
                    rows.first.get_by_role("button", name="Move down:", exact=False).press("Enter")
                assert ordered.value.ok
                expect(rows.first).to_have_attribute("data-race-id", second_id)
                refresh()
                expect(rows.nth(1)).to_have_attribute("data-race-id", first_id)
                with page.expect_response(lambda r: r.url.endswith("/race-order")) as ordered:
                    rows.nth(1).locator(".race-drag").drag_to(rows.first.locator(".race-drag"))
                assert ordered.value.ok
                expect(rows.first).to_have_attribute("data-race-id", first_id)
                page.locator("#history-sort").select_option("course")
                page.locator("#history-direction").select_option("asc")
                expect(page.locator("#history tbody tr").first).to_contain_text("Mario Circuit")
                page.locator("#history-direction").select_option("desc")
                expect(page.locator("#history tbody tr").first).to_contain_text("Next queued race")
                page.screenshot(path=str(evidence_path / "dashboard.png"), full_page=True)
                tab("Standings")
                page.wait_for_timeout(220)  # Finish the view entrance before the visual evidence.
                page.screenshot(path=str(evidence_path / "dashboard-themed-standings.png"), full_page=True)
                # The browser uses the exact colors computed for exports and OBS.
                displayed = api("/api/state")["display_standings"]
                if page.locator("#teams tbody tr").count():
                    team = next(team for team in displayed["teams"] if team["id"] is not None)
                    assert page.locator("#teams tbody tr").first.evaluate("row => row.style.getPropertyValue('--team-color')") == team["color"]
                tab("Settings")
                page.wait_for_timeout(220)
                page.screenshot(path=str(evidence_path / "dashboard-settings.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(evidence_path / "dashboard-settings-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                # Pending captures from an earlier session never revive its roster.
                current_players = api("/api/state")["players"]
                tab("Race history")
                page.locator("#history-folder").select_option("")
                page.locator(f"#history tr[data-race-id='{first_race_id}']").get_by_role("button", name="Review / correct").click()
                old_choices = page.locator("#placements select").first.evaluate("select => [...select.options].map(option => option.value).filter(Boolean)")
                assert set(old_choices) == set(ids), "Archived corrections must not offer the selected foreign session roster."
                assert not set(old_choices) & {player["id"] for player in current_players}
                page.get_by_role("button", name="Close editor", exact=True).click()
                # A later queued card updates exact identity suggestions after enrollment.
                with app.lock:
                    match_race = app.store.create_race(str(uuid4()), "Exact identity test")
                    match_candidate = {"id": str(uuid4()), "race_id": match_race["id"], "course": match_race["course"],
                                       "rows": [{"position": index + 1, "name": " " + player["name"] + " "}
                                                for index, player in enumerate(current_players)]}
                    app.capture.pending.append(match_candidate)
                refresh()
                for index, player in enumerate(current_players):
                    expect(page.locator(".capture-placement select").nth(index)).to_have_value(str(player["id"]))
                tab("Capture & review")
                identity = page.locator(".capture-placement select").first
                assert identity.evaluate("select => [...select.options].slice(1).map(option => option.text).every((name, i, names) => i === 0 || names[i - 1].localeCompare(name) <= 0)")
                # A manual new identity choice is intentional and persists through refresh.
                identity.select_option("")
                refresh()
                expect(identity).to_have_value("")
                # Editing an automatically matched name re-runs exact matching.
                second_name = page.locator(".capture-placement input").nth(1)
                second_name.fill("Unmatched new name")
                expect(page.locator(".capture-placement select").nth(1)).to_have_value("")
                second_name.fill(current_players[1]["name"])
                expect(page.locator(".capture-placement select").nth(1)).to_have_value(str(current_players[1]["id"]))
                # A duplicate roster name must not silently choose either identity.
                duplicate = api("/api/players", "POST", {"name": current_players[2]["name"]})
                refresh()
                expect(page.locator(".capture-placement select").nth(2)).to_have_value("")
                api(f"/api/players/{duplicate['id']}", "DELETE", {})
                page.locator(".capture-placement input").nth(3).fill("Later enrolled identity")
                expect(page.locator(".capture-placement select").nth(3)).to_have_value("")
                later_player = api("/api/players", "POST", {"name": "Later enrolled identity"})
                refresh()
                expect(page.locator(".capture-placement select").nth(3)).to_have_value(str(later_player["id"]))
                api(f"/api/players/{later_player['id']}", "DELETE", {})
                # Remove the controlled card directly without committing another score.
                with app.lock:
                    app.capture.pending.remove(match_candidate)
                refresh()

                # Exercise video import/cancel and file upload at a controlled job boundary.
                import_job = {"id": "browser-job", "status": "running", "path": "/tmp/browser-video.mkv", "progress_seconds": 25,
                              "duration_seconds": 100, "percent": 25, "observed_races": 2, "observed_results": 1,
                              "name_engine": "auto", "resolved_name_engine": "qwen", "name_engine_status": "ready"}
                imports = []
                def import_state(route):
                    response = route.fetch()
                    body = response.json()
                    body["imports"] = {"active": import_job if imports and import_job["status"] == "running" else None,
                                       "jobs": [import_job] if imports else []}
                    route.fulfill(response=response, json=body)
                def import_request(route):
                    imports.append(route.request.post_data_json)
                    selected = imports[-1]["name_engine"]
                    import_job.update(status="running", name_engine=selected,
                                      resolved_name_engine="qwen" if selected == "auto" else selected)
                    route.fulfill(json=import_job)
                def cancel_import(route):
                    import_job["status"] = "cancelled"
                    route.fulfill(json=import_job)
                page.route("**/api/state", import_state)
                page.route("**/api/imports", import_request)
                page.route("**/api/imports/browser-job/cancel", cancel_import)
                tab("Videos")
                page.locator("#import-video input[name=path]").fill("/tmp/browser-video.mkv")
                submit("import-video", "/api/imports")
                assert imports[0]["path"] == "/tmp/browser-video.mkv" and imports[0]["stride"] == 0.5
                expect(page.locator("#import-jobs")).to_contain_text("25%")
                expect(page.locator("#import-jobs")).to_contain_text("results detected")
                expect(page.locator("#import-jobs")).not_to_contain_text("results awaiting review")
                with page.expect_response(lambda response: response.url.endswith("/browser-job/cancel")):
                    page.get_by_role("button", name="Cancel import").click()
                expect(page.locator("#import-jobs")).to_contain_text("cancelled")
                for engine, (label, _) in qwen_choices.items():
                    requests_before = len(imports)
                    page.locator("#import-video select[name=name_engine]").select_option(engine)
                    warning = page.locator("#import-nvfp4-warning")
                    if engine.endswith("nvfp4"):
                        expect(warning).to_be_visible()
                        expect(warning).to_contain_text("NVIDIA Blackwell")
                    else:
                        expect(warning).to_be_hidden()
                    refresh()
                    expect(page.locator("#import-video select[name=name_engine]")).to_have_value(engine)
                    assert len(imports) == requests_before
                    submit("import-video", "/api/imports")
                    assert imports[-1]["name_engine"] == engine
                    expect(page.locator("#import-jobs")).to_contain_text(f"Name reader: {label}")
                    with page.expect_response(lambda response: response.url.endswith("/browser-job/cancel")):
                        page.get_by_role("button", name="Cancel import").click()
                    expect(page.locator("#import-jobs")).to_contain_text("cancelled")
                upload_urls = []
                uploads = []
                def upload_request(route):
                    uploads.append((route.request.headers.get("x-filename"), route.request.post_data_buffer))
                    upload_urls.append(route.request.url)
                    route.fulfill(json=import_job)
                page.route("**/api/imports/upload?*", upload_request)
                page.locator("#import-video select[name=name_engine]").select_option("qwen-4b-nf4")
                expect(page.locator("#import-nvfp4-warning")).to_be_hidden()
                page.locator("#import-video input[name=path]").fill("")
                page.locator("#import-video input[type=file]").set_input_files({"name": "browser video.mkv", "mimeType": "video/x-matroska", "buffer": b"browser video"})
                with page.expect_response(lambda response: "/api/imports/upload?" in response.url):
                    page.locator("#import-video button").click()
                expect(page.locator("#import-video button")).to_be_enabled()
                assert uploads == [("browser%20video.mkv", b"browser video")]
                assert "name_engine=qwen-4b-nf4" in upload_urls[-1]
                # Focused upload controls and unsaved drafts cannot freeze job progress.
                import_job.update(status='running', progress_seconds=55, percent=55,
                                  name_engine='auto', resolved_name_engine='qwen')
                page.locator('#auto-refresh').check()
                page.locator('#import-video input[name=path]').fill('/tmp/unsaved-draft.mkv')
                page.locator('#import-video input[name=path]').focus()
                expect(page.locator('#import-jobs')).to_contain_text('55%', timeout=7000)
                expect(page.locator('#import-jobs')).to_contain_text('Name reader: Qwen3-VL-8B BF16 (automatic)')
                expect(page.locator('#import-video input[name=path]')).to_have_value('/tmp/unsaved-draft.mkv')
                expect(page.locator('#import-video input[name=path]')).to_be_focused()
                # Both draft-protected and complete redraws retain the current scroll position.
                page.set_viewport_size({'width': 900, 'height': 500})
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                scroll_y = page.evaluate('window.scrollY')
                assert scroll_y > 0
                import_job.update(progress_seconds=65, percent=65)
                expect(page.locator('#import-jobs')).to_contain_text('65%', timeout=7000)
                assert abs(page.evaluate('window.scrollY') - scroll_y) < 2
                page.locator('#import-video input[name=path]').blur()
                import_job.update(progress_seconds=75, percent=75)
                expect(page.locator('#import-jobs')).to_contain_text('75%', timeout=7000)
                assert abs(page.evaluate('window.scrollY') - scroll_y) < 2
                page.locator('#auto-refresh').uncheck()
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.unroute("**/api/state", import_state)
                page.unroute("**/api/imports", import_request)
                page.unroute("**/api/imports/browser-job/cancel", cancel_import)
                page.unroute("**/api/imports/upload?*", upload_request)
                page.locator("#import-video").evaluate("form => form.reset()")
                refresh()
                with app.lock:
                    old_pending_race = app.store.create_race(str(uuid4()), "Pending before session change")
                    app.capture.pending.append({"id": str(uuid4()), "race_id": old_pending_race["id"],
                                                "course": old_pending_race["course"],
                                                "rows": [{"position": index + 1, "name": player["name"], "confidence": 90}
                                                         for index, player in enumerate(current_players)]})
                refresh()
                expect(page.locator("#review-count")).to_have_text("1 waiting")
                create_session("Fresh session after pending capture")
                tab("Capture & review")
                expect(page.locator("#capture-reviews")).to_contain_text("Archived capture")
                expect(page.locator("#players")).to_contain_text("Add the 12 players")
                with page.expect_response(base + "/api/capture/review") as response:
                    page.get_by_role("button", name="Confirm identities and count points").click()
                assert response.value.ok, response.value.text()
                expect(page.locator("#review-count")).to_have_text("0 waiting")
                assert api("/api/state")["players"] == []
                assert api("/api/state")["teams"] == []
                # First confirmation groups reviewed initials, not the original OCR text.
                initial_names = ['Alice', 'amy', 'Bob', 'ben', 'Émile', 'émilie',
                                 '選手一', '選手二', '☆Star', '7Seven', 'Charlie', 'chris']
                with app.lock:
                    first_review = app.store.create_race(str(uuid4()), "First roster")
                    app.capture.pending.append({"id": str(uuid4()), "race_id": first_review["id"],
                                                "course": first_review["course"],
                                                "rows": [{"position": index + 1, "name": name}
                                                         for index, name in enumerate(initial_names)]})
                refresh()
                expect(page.locator("#capture-reviews")).to_contain_text("First roster: confirming creates editable teams")
                assert api("/api/state")["room"]["points"] == 0
                assert api("/api/state")["teams"] == []
                page.get_by_label("Name for new player", exact=True).first.fill("bAlice")
                with page.expect_response(base + "/api/capture/review") as response:
                    page.get_by_role("button", name="Confirm identities and count points").click()
                assert response.value.ok, response.value.text()
                expect(page.locator("#review-count")).to_have_text("0 waiting")
                grouped = api("/api/state")
                labels = {team["id"]: team["name"] for team in grouped["teams"]}
                assert [labels.get(player["team_id"]) for player in grouped["players"]] == [
                    'B', 'A', 'B', 'B', 'É', 'É', '選', '選', '☆', '7', 'C', 'C']
                assert grouped["room"]["points"] == 82
                tab("Standings")
                first_player = grouped["players"][0]
                with page.expect_response(base + f"/api/players/{first_player['id']}"):
                    page.get_by_label("Team for bAlice", exact=True).select_option("")
                assert api("/api/state")["players"][0]["team_id"] is None
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(evidence_path / "dashboard-mobile.png"), full_page=True)
                assert errors == [], f"Browser errors: {errors}"
                overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
                    .filter(node => node.getBoundingClientRect().right > innerWidth)
                    .map(node => ({tag: node.tagName, id: node.id, className: node.className,
                                  right: Math.round(node.getBoundingClientRect().right)}))""")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Mobile layout overflows viewport: {overflow}"
                browser.close()
                print("PASS: Chromium tabs/keyboard/persistence, 50 artwork-free palettes, shared standings colors, overlay configuration/highlight and custom team color persistence, Qwen precision/model selectors/VRAM guidance/NVFP4 warnings/preserved drafts/capture-import-upload payloads/mobile layout, fake camera selection/start/stop/OBS clock/no-device handling, OBS screenshot mode/source/drafts/saved credentials/errors/no camera discovery, empty/actual camera errors, saved-password reuse/forget/draft preservation, suspected-result evidence/manual warnings/timing and current-race review transition, results/scoring, chapter export, stale-tab draft protection/reload recovery, new/load/delete sessions, archived correction/review, player/team deletion, team/folder rename, folder filter, ascending/descending sorting, reload persistence, exact identity matching after queued enrollment, first-reviewed-roster editable initial teams and deleted-manual-roster guard, alphabetical choices, team gaps/984-point room total, course text without bundled images, collapsible folders, Lounge links, PNG preview/copy/snapshot isolation/cleanup/mobile/errors and download, penalties, evidence, TXT/MD settings, individual race deletion, populated nonselected folder deletion, safe video cleanup/error handling, import/upload/cancel controls, mobile layout; no browser errors.")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()


if __name__ == "__main__":
    main()
