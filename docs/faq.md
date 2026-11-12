---
title: "Common questions"
weight: 80
---

# Common questions

## Do I need a GPU?

No. Manual scoring needs none. For screenshot reading, choose the CPU reader or basic Tesseract. [Reader choices](recognition.md)

## Do I need OBS?

Only for the OBS capture/overlay features. You can enter results manually or import a recording without it.

## Does it play the game or track live positions?

No. It tracks results **after a race**, not positions during the race. It does not control the game.

## Do I need to enter every result myself?

Not usually. Capture and imports can read results for you. In mogi modes, check the names and finishing order, then confirm. Nothing counts while it is pending.

## Can I use other team sizes?

Yes. Assign the twelve players to your teams in Standings. For individual scoring, choose Mogi FFA. Each scored mogi race still needs twelve different players.

## What do the overlay gaps mean?

`-5 / -15` means **5 points behind the team above** and **15 behind first place**.

## Will it upload my recordings?

Not to a cloud service. The file picker makes a copy on the computer running the tracker. Import by local path to avoid that copy. Model downloads happen during setup; recognition runs locally.


## Does a Lounge ID submit my table?

No. It labels your report and links to a table. It does not submit or edit anything on Lounge.

## Are downloadable installers available?

Not yet. Use [guided setup](getting-started.md). Linux AppImage and Windows packaging are tracked in [issue #2](https://gitlab.sandal.tech/ebuttonsdude/mkw-mogi-tracker/-/work_items/2).

## Can I move saved sessions to another installation?

Use [Settings → Session backups](data-and-backups.md) to download selected sessions and preview an import. Web restores add copies without replacing existing sessions and exclude saved credentials.

## Can I keep my mkw-livesplit-vos sessions?

Yes. The [migrator](migrator.md) copies sessions and assets without changing the original. Its terminal menu shows counts and size estimates and lets you choose categories or everything. Videos and models are optional; name conflicts offer Rename, Skip or explicit Overwrite. Local migration handles the selected history in one run without the web ZIP backup limits.

## Where are the developer details?

In [Technical reference](technical/_index.md). You do not need them to use the app.
