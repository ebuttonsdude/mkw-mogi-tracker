---
title: "Move from mkw-livesplit-vos"
weight: 90
---

# Move from mkw-livesplit-vos

The migrator **copies** your saved sessions into MKW-Mogi-Tracker. It does not delete or change the original project. Imported sessions receive new identities. If a session name or asset filename already exists, choose **Rename**, **Skip** or **Overwrite**. Overwrite replaces only the matched target object; the original project stays untouched.

## Choose what to migrate

1. Close both trackers. Keep your original folder as a backup.
2. Open a terminal in your **MKW-Mogi-Tracker** folder.
3. Launch the interactive migrator, replacing the example source path:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos"
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

4. The terminal lists **sessions, evidence images, pictures/themes, fonts, uploaded videos and portable models**, with counts and approximate source sizes. Type a category number to toggle it, or **a** to select everything. Videos and models are initially off. Evidence requires sessions; selecting evidence also selects sessions.
5. Type **p** to preview, **m** to review and migrate, or **q** to cancel. Nothing is imported until you type **yes** at the final confirmation.
6. Resolve conflicts in the terminal. **Rename** lets you type a new name (or accept an automatic one); **Skip** keeps the target and leaves that incoming item out; **Overwrite** replaces the matched target item. Choose **Abort** to leave everything unchanged. Ambiguous duplicate session names must be renamed or skipped. Theme conflicts also offer **Overwrite all remaining theme conflicts**; this choice applies only to themes, not sessions or files.
7. Start the tracker and load an imported folder in **Race history**. Check each mode you used.

**Local migration has no ZIP archive quota or fixed session/evidence-count limit.** It processes the selected history in one run; you do not need to divide it into batches. Evidence, recordings and model files are copied incrementally. Record metadata still needs memory proportional to your history, and temporary database copies and rollback journals require additional disk space. The menu shows available destination space and the temporary database location. Estimates are based on source sizes before conflict choices, not exact final disk or peak RAM use.

Unavailable evidence is reported in the inventory. Selecting it requires the missing or unsafe files to be repaired first; excluding evidence still lets you migrate the sessions and other selected categories. Invalid records and unsafe paths are rejected rather than silently discarded. The web UI's ZIP backup limits remain separate.

If the original used a custom data folder, add `--source-data-dir "/path/to/original-data"`. To use a custom target, add `--data-dir "/path/to/new-data"` and use that same option when starting the tracker. Source and target must be separate directories.

## Commands without the menu

For a JSON preview without any prompts:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --everything --preview
```

For an explicit noninteractive import of all supported data:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --everything --apply --non-interactive --conflict rename
```

Use `--only sessions,evidence`, for example, to select categories without the menu. The available names are `sessions,evidence,pictures,fonts,videos,models`. `--only evidence` also includes its required sessions. The existing `--include-videos` and `--include-models` options still work.

`--apply` explicitly authorizes import without the menu's final confirmation. Without it, redirected input or `--non-interactive` only previews. For unresolved conflicts in scripts, choose `--conflict rename`, `--conflict skip` or `--conflict overwrite`; otherwise the import stops without writing. **`--conflict overwrite --apply` replaces every matched target item**, so preview first.

## What comes across?

- Mogi Teams, FFA and Worldwides sessions, folders, roster, results and corrections, penalties, recording references, chapters and saved race evidence.
- Character pictures can replace their same-name built-in themes, so the picker shows one entry with the imported icon instead of a duplicate. Choose **Overwrite** individually or **Overwrite all remaining theme conflicts**. Overwrite replaces all exact-same-name target theme copies; unrelated or renamed themes stay untouched. Built-in overrides retain their original IDs and do not use the fifty custom-theme slots. Legacy character pictures use the tracker's built-in palettes; saved source theme records keep their own colors. The original selected theme is considered first. Pictures exceeding picker image or total-library limits are retained as assets with a warning. Original character pictures and custom-theme JSON are also preserved under `imported-assets/`, unless their file conflicts are skipped. Course pictures are retained under `imported-assets/track-icons`; recognizable filenames also become local course thumbnails. Saved source course associations take priority over static filenames, then previously retained source pictures. Unknown filenames, invalid images and images exceeding display limits remain retained assets with summary warnings; assign them manually in **Settings → Course pictures**.
- TTF/OTF fonts are copied without an aggregate size quota into the target’s `fonts` folder. Supported files appear in the font selector; files above its size limit are retained with a warning. The original report font is selected when supported and no target preference has been saved; existing choices, including the default font, are kept.
- Different files with the same name prompt for a conflict choice. Existing theme choices remain selected; when overwriting removes a duplicate selected theme, selection follows its replacement.

OBS passwords, private connection files, capture queues, environments and executable paths are **not** imported. Connect OBS again in the new tracker. Pending races remain unscored and need confirmation.

## Repair duplicate migrated themes

If an earlier migration added a second copy of each character, close the tracker and run a **pictures-only** migration. Preview first:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --only pictures --preview
```

To replace all exact-same-name target themes with the source versions and remove their duplicate picker entries:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --only pictures --apply --theme-conflict overwrite --conflict skip
```

This does **not** import sessions again. `--theme-conflict overwrite` replaces matching themes, including any customization under those exact names; differently named custom themes remain. `--conflict skip` keeps existing retained asset files when their contents differ, while the source picture still supplies the replacement theme icon. Add your usual `--data-dir` or `--source-data-dir` if needed. The source stays unchanged.

For individual choices, omit both conflict flags. At theme prompts, **w** overwrites all remaining theme conflicts. To remove an imported override later, select its theme in Settings, expand **Create or edit a theme**, choose **Edit selected theme**, then **Restore built-in**.

## Add course pictures after an earlier migration

Close both trackers, then run a pictures-only preview:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --only pictures --preview
```

To activate missing course pictures while leaving existing themes, course pictures and retained files alone:

```sh
.venv/bin/python -m mkw_tracker.migrate "/path/to/mkw-livesplit-vos" --only pictures --apply --theme-conflict skip --conflict skip
```

This does not import sessions again. Already copied raw files do not prevent their source pictures from being activated.

Legacy coded filenames such as `track_011MBC.png` use the old app’s exact course associations; you do not need to rename those files. If an earlier run reported `course_images: 0` and “no exact known course filename match” for these codes, update the tracker and rerun the command above. A complete set activates 40 pictures. Start the tracker again and open **Settings → Course pictures** to see them; **Reload saved pictures** only reloads registered pictures, not unassociated raw files.

To explicitly replace existing course pictures too, add **`--course-image-conflict overwrite`**. Interactive course-image conflicts offer Skip, Overwrite or Abort. A general `--conflict rename` keeps an existing course association and warns; raw files still follow the chosen file policy.

The migrator also carries a source `course-images.json` library and pictures previously retained under its data folder. Retained source files are copied under `imported-assets/track-icons/retained`. Check the summary for unmatched names, then use [Course pictures](user-guide.md#course-pictures) to associate those files manually. The source remains unchanged.

## Optional recordings and models

Videos are **off by default**. Select them in the menu, choose everything, or add `--include-videos` to copy uploaded recordings into `imported-videos`. Original recordings referenced by a local path stay where they are; back them up separately. Copied recordings can be selected by local path in Videos when needed.

Select models in the menu, choose everything, or add `--include-models` to reuse portable model data without copying Python environments or reader executables. Model files normally go into `.local/models`, alongside the target tracker data directory. If you use a custom data directory, pass `--model-dir "/path/to/mkw-mogi-tracker/.local/models"` to put them where reader setup expects them. Run reader setup in the new project to install its environment. If an existing model file differs, choose a separate model destination, skip it, or explicitly overwrite it. Individual weight shards cannot be renamed because model indexes depend on their names.

Pictures, fonts and model files remain local runtime data. Only copy assets you are entitled to use.

[Back up sessions](data-and-backups.md) · [Common questions](faq.md)
