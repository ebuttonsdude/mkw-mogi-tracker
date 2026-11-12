"""Optional isolated Chromium checks for disclosures, history and hardware reminders."""
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from mkw_tracker.server import Application, make_server
from mkw_tracker.themes import resolve_theme


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        app = Application(Path(temporary) / 'tracker.sqlite3', temporary)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with patch.object(app.cameras, 'enumerator', return_value=[]), sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_function('() => state !== null && !refreshing')
                page.locator('#auto-refresh').uncheck()
                page.locator('#tab-settings').click()
                assert page.evaluate('''() => {
                    const selectors = ['#theme-settings', '#font-details', '#backup-section', '#experimental-section'];
                    return selectors.every((selector, index) => !index ||
                      (document.querySelector(selectors[index - 1]).compareDocumentPosition(
                        document.querySelector(selector)) & Node.DOCUMENT_POSITION_FOLLOWING));
                }''')
                expect(page.locator('#view-settings #overlay-settings')).to_have_count(0)
                expect(page.locator('#view-overlays #overlay-settings')).to_have_count(1)
                for selector in ('#font-details', '#backup-section', '#experimental-section'):
                    expect(page.locator(selector)).not_to_have_attribute('open', '')
                    page.locator(selector + ' > summary').click()
                page.evaluate('refresh()')
                page.wait_for_function('() => !refreshing')
                for selector in ('#font-details', '#backup-section', '#experimental-section'):
                    expect(page.locator(selector)).to_have_attribute('open', '')
                expect(page.locator('#experimental-section #yolo-mode')).to_be_visible()
                expect(page.locator('#experimental-section #external-settings')).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#tab-capture').click()
                expect(page.locator('#capture-name-engine-help')).to_be_hidden()
                engine = page.locator('#camera-settings [name=name_engine]')
                engine.select_option('qwen-nvfp4')
                warning = page.locator('#capture-nvfp4-warning')
                expect(warning).to_be_visible()
                warning.get_by_role('button', name='Dismiss hardware reminder').click()
                expect(warning).to_be_hidden()
                page.locator('#capture-name-engine-details > summary').click()
                expect(page.locator('#capture-name-engine-details')).to_contain_text('RTX 5070')
                page.reload()
                page.wait_for_function('() => state !== null && !refreshing')
                engine.select_option('qwen-nvfp4')
                expect(warning).to_be_hidden()
                engine.select_option('qwen36-nvfp4')
                expect(warning).to_be_visible()
                # Setup warnings cannot be dismissed along with the static hardware reminder.
                page.evaluate("state.reader_setup_warning = 'Reader setup needs repair'; renderNameEngineWarning('camera-settings', 'capture-nvfp4-warning')")
                warning.get_by_role('button', name='Dismiss hardware reminder').click()
                expect(warning).to_have_text('Reader setup needs repair')
                page.evaluate("delete state.reader_setup_warning; renderNameEngineWarning('camera-settings', 'capture-nvfp4-warning')")
                for mode in ('mogi', 'ffa', 'ww'):
                    page.locator('#tracker-mode').select_option(mode)
                    expect(page.locator('body')).to_have_attribute('data-tracker-mode', mode)
                    page.wait_for_function('() => !refreshing')
                    with app.lock:
                        app.store.create_folder(f'{mode} second')
                        app.store.create_folder(f'{mode} third')
                        app.store.create_race(f'{mode}-draft', 'Original course')
                    page.evaluate('refresh()')
                    page.wait_for_function('() => !refreshing')
                    page.locator('#tab-history').click()
                    folders = page.evaluate('state.folders.map(folder => String(folder.id))')
                    assert len(folders) >= 3
                    page.locator('#history-folder').select_option(folders[0])
                    expect(page.locator('#history details.history-folder')).to_have_count(1)
                    # Collapse does not rebuild history or clear an active draft.
                    page.evaluate("id => openTiming(id)", f'{mode}-draft')
                    page.locator('#race-timing [name=course]').fill('Unfinished course correction')
                    page.evaluate("document.querySelector('#history').dataset.marker = 'kept'")
                    page.locator('#history-collapse-all').click()
                    assert page.evaluate("id => timingEditing === id && document.querySelector('#history').dataset.marker === 'kept'", f'{mode}-draft')
                    expect(page.locator('#race-timing [name=course]')).to_have_value('Unfinished course correction')
                    page.locator('#cancel-timing').click()
                    saved = page.evaluate("JSON.parse(localStorage.getItem('mkw-folder-expansion'))")
                    assert all(saved[folder] is False for folder in folders)
                    page.locator('#history-folder').select_option('')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    page.evaluate('refresh()')
                    page.wait_for_function('() => !refreshing')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    page.reload()
                    page.wait_for_function('() => state !== null && !refreshing')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    # Native toggle is deferred: redraw in the same task before it fires.
                    page.evaluate('''() => {
                        document.querySelector('#history details.history-folder').open = true;
                        renderHistory();
                    }''')
                    expect(page.locator('#history details[open]')).to_have_count(1)
                    page.reload()
                    page.wait_for_function('() => state !== null && !refreshing')
                    expect(page.locator('#history details[open]')).to_have_count(1)
                    page.evaluate('''() => {
                        document.querySelector('#history details.history-folder').open = false;
                        renderHistory();
                    }''')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    page.reload()
                    page.wait_for_function('() => state !== null && !refreshing')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    # At narrow widths a summary's center may be its Rename button.
                    # Exercise wrapped font metrics but click the title, not that action.
                    page.evaluate("document.querySelector('#history').style.fontFamily = 'monospace'")
                    heading = page.locator('#history details.history-folder > summary').first
                    (heading if mode == 'ww' else heading.locator('span').first).click()
                    expect(page.locator('#history details[open]')).to_have_count(1)
                    heading.press('Enter')
                    expect(page.locator('#history details[open]')).to_have_count(0)
                    heading.press('Enter')
                    expect(page.locator('#history details[open]')).to_have_count(1)
                    page.evaluate('refresh()')
                    page.wait_for_function('() => !refreshing')
                    expect(page.locator('#history details[open]')).to_have_count(1)
                # Runtime overrides edit/restore under the original built-in ID.
                with app.lock:
                    original = resolve_theme('luigi')
                    override = {key: original[key] for key in ('id', 'name', 'tokens', 'team_colors')}
                    override['tokens'] = {**override['tokens'], 'accent': '#123456'}
                    app.theme_library._write({'luigi': override})
                    app.update_settings({'theme': 'luigi'})
                page.evaluate('refresh()')
                page.wait_for_function('() => !refreshing')
                page.locator('#tab-settings').click()
                page.locator('#custom-theme-editor > summary').click()
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#theme-edit-selected').click()
                expect(page.locator('#theme-delete')).to_have_text('Restore built-in')
                expect(page.locator('#theme-update')).to_be_enabled()
                expect(page.locator('[data-token=accent]')).to_have_value('#123456')
                confirmations = []
                page.on('dialog', lambda dialog: (confirmations.append(dialog.message), dialog.accept()))
                page.locator('#theme-delete').click()
                expect(page.locator('#theme-editor-status')).to_contain_text('Built-in theme restored')
                expect(page.locator('#character-theme')).to_have_value('luigi')
                expect(page.locator('[data-token=accent]')).to_have_value(original['tokens']['accent'])
                expect(page.locator('#theme-delete')).to_be_disabled()
                expect(page.locator('#theme-delete')).to_have_text('Delete saved theme')
                assert any('Sessions remain unchanged' in text for text in confirmations)
                assert app.settings['theme'] == 'luigi'
                assert 'luigi' not in app.theme_library.records
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()
    print('PASS: ordered Settings, default/polling disclosures, per-model reminder dismissal, three-mode filtered collapse/reload and mobile layout.')


if __name__ == '__main__':
    main()
