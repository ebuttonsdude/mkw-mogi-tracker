# Screenshot provenance

`standings.png` and `overlay.png` show the real local application with fictional
players, teams, and scores. Generated on 2026-11-07 using
`tools/docs_screenshots.py`, a temporary database, and Chromium. The displayed
filesystem path belongs to that disposable example, not a user's data folder.

No gameplay footage, Nintendo artwork, uploaded user icons, credentials, or
personal sessions are included. The overlay retains its transparent background.
These images illustrate the interface, not recognition accuracy or live OBS/CEF
validation. They belong to the repository/docs website only; application wheel
and source distributions remain image-free.

To regenerate, use the existing optional browser-test environment inside a
verified separate PID namespace. Example on Linux with namespace permission:

```sh
HOST_PID_NS=$(readlink /proc/self/ns/pid) \
unshare --pid --fork --mount --mount-proc sh -c '
  test "$(readlink /proc/self/ns/pid)" != "$HOST_PID_NS" || exit 1
  PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python tools/docs_screenshots.py
'
```

If isolation is unavailable, do not run process-lifecycle/browser cleanup tests
on the live desktop as a fallback.

## Name-review example

`name-review.png` is a cropped screenshot of the real app's first finishing-row
review controls, generated with fictional data by `tools/docs_screenshots.py
--only-review`. The temporary roster contains AApple; controlled reader output
says A Apple. Playwright selects the existing AApple identity through the actual
form. The original detected text remains visible and no result is confirmed.
This is a synthetic recognition scenario, not a measured reader error. No
gameplay image, personal name, credential, or runtime icon is included.

The `--only-review` flag leaves the existing standings/overlay PNGs untouched.

## Worldwides examples

`ww-standings.png` and `ww-overlay.png` show the real WW dashboard and overlay
using two fictional manually entered sessions, six races per session, and the
fictional player Comet. Course statistics use All WW sessions; Boo Cinema has
the lowest mean finishing position. Current-session ratings and the overlay
remain scoped to Example Worldwides. No recognition, gameplay screenshot,
hardware connection, personal record, or uploaded icon is involved.
The overlay PNG retains transparency.

Regenerate these two images only with `tools/docs_screenshots.py --only-ww`
inside verified PID isolation. Other documentation images remain untouched.
