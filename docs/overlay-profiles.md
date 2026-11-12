---
title: "Overlay sources and Lounge cards"
weight: 45
---

# Overlay sources and Lounge cards

## Make separate OBS sources

1. Open **Overlays**.
2. Enter a source name and a URL slug such as `player-cards`, then **Create source**.
3. Select the source before editing its display settings or content.
4. Copy its Browser source URL into OBS. Match the source's width and height.

`/overlay` remains the Default source. Named sources use `/overlay/<slug>`.
Each source has independent dimensions, layout, title, scrolling, highlights,
content assignments and display options. New sources copy today's default overlay
options; newly introduced options inherit the defaults. Optional source theme and
font overrides leave the dashboard, other sources and PNG reports unchanged.
Choose a source font from the available-font dropdown, or **Use dashboard font**
to inherit it. A saved font that is no longer available is labeled unavailable;
choose a replacement or inherit the dashboard font. A custom theme referenced by a source cannot be deleted
until that source chooses another theme.

All sources follow the application's current mode and active session. They are
not independent score databases or historical-session views. Up to 16 sources
(including Default) are supported. Source names can change; slugs are stable.
Deleting a named source makes its URL return not found; Default cannot be deleted.
Show/hide/toggle controls address the selected source only. Each source also has its own **Automatically hide during races / show after results** option and **Show / hide effect**. Enable automation separately on the sources that should disappear during racing; leave other sources visible. New or newly enabled sources wait for the next live race event. Visibility is temporary,
not a saved preference; application restart starts sources shown.

### Assign or move existing content

Open **Source content and Lounge cards**. Check the blocks this source should
show: header/uploaded icon, Mogi standings, WW player/current track, WW VR stats,
WW graph, or Lounge cards. The existing finer-grained display toggles still apply
inside these blocks. Multiple sources may display the same block.

**Move a content block** removes that block from every other source and assigns
it to the target in one saved update. The target keeps its own display and Lounge
preferences: configure its usernames and network opt-in separately. This also
lets you move content away from, or back to, `/overlay` without changing OBS URLs.
Use **Apply & preview** to save the selected source and display settings and start
a live preview. The separate save buttons still work. Unsaved source drafts survive
polling; switching sources asks before discarding them.

## Live preview

The **Overlays** tab includes a checkerboard preview. It stays unloaded until you
choose **Apply & preview** or explicitly refresh the saved preview. Unsaved typing
does not change the live source. Apply validates both forms before saving; if a
later display save fails after source preferences were saved, the error says so and your
draft remains available for correction.

The preview uses the real saved dimensions, scaled visually to fit the panel;
it does not change your OBS browser source. Match the width and height shown in
OBS. Source visibility still applies—use Show if the saved source is currently
hidden. Leaving Overlays or switching source/mode stops the preview. Reopen it
explicitly when you want to resume; other OBS sources remain unaffected.

## Optional MKW Lounge player cards

Cards work in **Mogi Teams and FFA**, not WW. They never alter the local roster,
player identities, results, penalties, or scores.

1. Select a source and assign the **Lounge player cards** block.
2. Enter one Lounge username per line (up to eight). These are manual Lounge
   usernames, independent of in-game display names and roster IDs.
3. Choose 12-player or 24-player Lounge, and a season (blank means current;
   `0` means preseason).
4. Select the metrics you want: username, division, MMR, leaderboard rank, season metrics,
   last-ten-event metrics, and/or derived seven-day metrics.
5. Explicitly check **Allow Lounge network requests for this source**, then save.
6. Open its overlay preview or use the source in OBS.

Requests send only these usernames, game/room size and season to
`https://lounge.mkcentral.com`. No screenshot, recording, local roster or
credential is sent. Saving settings alone does not fetch data. **Apply & preview** starts a live source,
so it can fetch when Lounge network access is enabled. Viewing an opted-in
OBS/overlay preview source schedules requests. The dashboard does not fetch Lounge
cards, and its colors/fonts always use global settings, even when Default has
source-specific overrides. WW disables all card requests, cancels queued/in-flight
work and hides cards. Turning off the block or network option does likewise when
no other enabled source uses the same lookup. Already sent requests cannot be
unsent.

Lounge lookup ignores ASCII spaces and case. The card shows the returned canonical
name, not a claimed in-game identity match. The numeric Lounge ID remains in the API data, not on the broadcast card. No approximate
roster matching occurs. Names and remote text render as text, never HTML.

### Style a compact broadcast card

Under **Source content and Lounge cards**, choose a **Background** (Aurora, Carbon,
Source theme, Transparent, Counter HUD or Uploaded image), **Border** (Rainbow, Division color, Source accent, None or Counter HUD double rim)
and **Shape** (Pill or Rounded), then save. Appearance is independent per source.
The source's width, font and panel opacity still apply.

For a compact badge like a stream nameplate, enable only **Username**, **MMR** and
**Division**. The name sits beside MMR and a colored division badge. Additional stat bubbles
share that same line between the name and rating, growing to use spare width and
wrapping only when needed—not because their time windows differ. Multiple players
get separate stacked cards. Built-in backgrounds are generated with CSS,
not downloaded artwork; Transparent leaves the card background clear.

**Use Counter HUD style** selects a translucent charcoal background, rounded
double rim and subtle glossy highlight, inspired by the lap and coin counters.
It uses your selected font, not bundled game artwork or a game font. Save or
Apply to activate the preset. You can also combine the Counter HUD border with
any other background.

To use your own picture, choose a **Custom card background**, then click
**Upload / replace background**. PNG, JPEG and WebP still images up to 4 MiB are
supported (no SVG or animation; maximum input dimensions 4096 px and 16 megapixels).
Uploads are normalized to a metadata-free PNG at up to 1920 × 1080, preserving
aspect ratio; unusually complex images whose normalized PNG exceeds 4 MiB must
be reduced first. Each source keeps one image, with a 32 MiB total saved-library limit.
The card uses a centered cover crop, darkened for legibility; source panel opacity
also applies. The image never changes the transparent canvas outside the cards.

Uploading/replacing or removing an image saves that image change immediately.
Select **Uploaded image** and **Save source content** or **Apply & preview** to
activate it; other settings remain drafts until saved. Replacing an image already
in use updates that source immediately. Switching to a built-in preset retains
your upload; **Remove uploaded background** deletes only this source's saved
copy, never your original file. Missing images fall back to the source panel color.
Deleting a named source also removes its background.

Images stay in the local tracker data directory (`overlay-backgrounds.json`),
not in the repository, Lounge requests or exported session ZIPs. Session backups
and the existing picture migration do not transfer overlay preferences/backgrounds;
retain the original images or back up the tracker data directory separately.

Changes use **▲ +value**, **▼ -value** or **− 0**, alongside short labels.
**Win %** replaces the positive/non-positive count. Tiny scope labels identify
**SZN** (season), **L10** (last ten events; **L2** if only two are known),
**7D** (derived last seven days), and **ALL** (legacy combined room sizes). No verification timestamps, update
times or technical player IDs appear on the broadcast card. Loading, Unavailable
and Stale remain short status badges.

### Form and recommended dimensions

Enable **Form · last 5 events** for a compact green/red W/L sequence, oldest on
the left and newest on the right. W is positive MMR; L is zero or negative MMR.
It uses up to five verified, non-deleted events, not a reconstructed order from
the win percentage. Missing, incomplete or ambiguously ordered history shows
Unknown; a verified empty history shows No events.

The source editor shows a **Recommended Lounge size (estimated)** as you change
metrics, usernames or font size—even before saving. It estimates a compact strip
per player, with wrapping when needed. **Use recommended size** fills the display
width/height fields only; select **Save overlay settings** to persist them, then
match the dimensions in OBS. Save changed content/Lounge selections with their
separate save button too. It never changes OBS or saves automatically.

The estimate is local: it makes no Lounge requests and uses representative values.
A different font, longer returned name or future values can need extra room; check
the overlay preview. Sources with other visible content show a Lounge-only estimate
but cannot apply it automatically. Use a separate Lounge source or size mixed content
manually. Source dimensions support widths 240–1920 and heights 40–2160 pixels,
allowing thin strips without changing existing saved sizes.

### What the numbers mean

- **Division** is the API's rank name (for example Gold); **leaderboard rank** is
  a separate numeric placement. Missing/unplaced values show **Unknown**, not 0.
- **Win %** means positive-MMR events, not first-place finishes. Season rate uses
  the API's `winRate`; recent rate is positive events divided by positive plus
  non-positive events, using the actual count (up to ten). Zero-MMR events are
  non-positive. Missing counts or zero events show **Unknown**, not 0%.
- **Average event score** means an entire event/table score, not one race.
- **Season** metrics use the returned season and game. Seasons 0/1 use the legacy
  combined-room API; their season and last-ten metrics are labeled combined room
  sizes. From season 2, 12-player and 24-player stats are separate.
- **Form** sorts valid table verification timestamps, excludes deleted tables and
  requires complete history matching the event count. Equal timestamps have no
  proven tie-breaker, so ambiguous histories remain Unknown. Legacy form uses
  combined room sizes, like other season/recent metrics.
- **Last up to 10 events** means the API's last-ten verified-event statistics,
  possibly fewer than ten. It is not a ten-day window.
- **Derived last 7 days** is a trailing window ending at the successful fetch's
  UTC timestamp, not a calendar week. It uses table **verification** times, not
  race-played times. The window remains frozen with cached data, but its timestamps are not displayed.

Seven-day derivation uses the full returned season history, only `Table` entries,
and excludes all table IDs with a `TableDelete` entry. Legacy combined history is
filtered to the selected room size. Event MMR change excludes penalties/bonuses.
The app checks required history fields, unique table IDs and the remaining table
count against `eventsPlayed`. Missing, malformed, oversized or inconsistent
history makes these metrics unavailable rather than manufacturing zeroes. With
complete history and no matching events, event count and MMR change are 0;
rate and average are Unknown. Non-finite numbers and invalid text remain unknown
or fail the refresh safely. This is a local derivation, not a native weekly API
aggregate, and depends on upstream continuing to return complete history.

### Freshness and failures

The in-memory cache refreshes at most every five minutes per username/season/room
size. Failures retry no faster than once per minute. A failed refresh preserves
the last successful card with a compact **Stale** badge; an initial
failure shows unavailable. Switching usernames or seasons never relabels old data.
No network cache survives application restart.

One background worker handles at most 16 queued lookups and 128 cached entries.
Each request has an eight-second total deadline and a 2 MiB response limit.
DNS has its own bounded resolver gate, independent of recognition. HTTP/TLS work
never runs on the capture thread or under the shared application lock. Requests
use one fixed HTTPS origin/path, encoded query values, certificate/hostname
verification and pinned public DNS peers, without proxy environment settings,
redirect following or API-supplied URLs. There are no new dependencies.

## API routes

Existing `/api/overlay` and `/api/overlay/show|hide|toggle` retain Default behavior.
Named reads use `/api/overlay/<slug>`; named controls use
`POST /api/overlay/<slug>/show|hide|toggle` with `{}`.

- `GET /api/overlay-profiles`: source records with effective settings and revision.
- `POST /api/overlay-profiles`: `{ "slug": "cards", "name": "Player cards" }`.
- `PATCH /api/overlay-profiles/<slug>`: partial `name`, `overlay`, `theme`,
  `report_font`, `content` or `lounge` options. Default display options still use
  `PATCH /api/settings` with `overlay` for backward compatibility.
- `DELETE /api/overlay-profiles/<slug>` with `{}`: delete a named source.
- `POST /api/overlay-profiles/move`: `{ "block": "standings", "target": "cards" }`.

Mutations retain the existing Host/Origin and tracker-mode checks. Non-Mogi calls
must include `expected_tracker_mode` as with other dashboard writes. Saved source
configuration is local `overlay-profiles.json`; it is not a session backup.

## Official API and source evidence

Research checked on 2026-11-09. The public [official OpenAPI v1 schema](https://lounge.mkcentral.com/swagger/v1/swagger.json)
defines `GET /api/player/details?name=...&game=mkworld12p&season=3`.
The website's `p=12` is not an API parameter. Website `PlayerDetails/<id>?season=3&p=12` pages are not JSON endpoints.

Primary upstream source was inspected at commit
`1e1fd4b195e80a18939a80da6e31fa545c1ad83b`:

- [Controller and lookup parameters](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Controllers/PlayersController.cs)
- [Response fields and history schema](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Models/ViewModels/PlayerDetailsViewModel.cs)
- [Positive-MMR win definition](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Stats/PlayerEventData.cs)
- [Season/last-ten aggregates](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Stats/PlayerLeaderboardData.cs)
- [Complete-history cache](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Stats/PlayerDetailsCache.cs)
- [Verification timestamps and table deletions](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Stats/PlayerDetailsViewModelService.cs)
- [Username normalization](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Utils/PlayerUtils.cs)
- [Website season/game mapping](https://github.com/VikeMK/Lounge-API/blob/1e1fd4b195e80a18939a80da6e31fa545c1ad83b/src/Lounge.Web/Pages/PlayerDetails.cshtml.cs)

The deployed commit is not advertised; upstream behavior can change. The verified
endpoint advertises a 180-second public cache lifetime; this app uses a more
conservative five-minute refresh. No official service SLA is assumed. The tests
use mocked Lounge responses and disposable local servers, not live OBS/CEF,
real capture or production player accounts.
