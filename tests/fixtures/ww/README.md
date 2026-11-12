> This derived repository omits the gameplay images described below. These are historical provenance notes; image-dependent tests must skip when local fixtures are absent.

# Worldwides recognition references

These small still-image fixtures come from the two recordings supplied by the
repository owner for GitLab issue #20. The original recordings remain local and
ignored under `video-samples/`; no video is included in this fixture set.

`reference.json` records the source filename, file-relative sampling time, resized
frame size, crop rectangle and manually checked expected values. Frames were
sampled with OpenCV at the listed millisecond offsets and resized from 2560×1440
to 1280×720, except the explicitly marked FFmpeg scale regression at 500 seconds. All fixtures use WebP, matching the existing source-package fixture format.
Results-table crops are lossless; full-frame course and negative gameplay
references use quality 95. Tests place each crop in its original coordinates on
a black frame. These are Nintendo game screenshots, not original application
artwork; rights remain with their respective holders.

The results cover a local player pinned below the visible top twelve, an ordinary
fifth-place row with symbol/Japanese name, and a colored first-place glyph.
Change-phase VR is an observation, not proof of the original rating. In
particular, hardname at 386 seconds is already counting down from 8566/+78 and
shows the intermediate pair 8631/+13. Temporal validation must not mistake that
remainder for the full race change. Final-phase samples have no remaining delta text. The test using only two
rows is synthetic and does not establish support for an unseen two-player layout.

| Recording | Time | Manually checked observation |
| --- | --- | --- |
| easyname | 90 s | Place 18, VR 8805, change −46 |
| easyname | 94 s | Place 18, final VR 8759 |
| hardname | 96 s | Place 5, VR 8576, change +52 |
| hardname | 98 s | Place 5, final VR 8628 |
| hardname | 384.5 s | Place 1, initial VR 8566, change +78 |
| hardname | 386 s | Place 1, intermediate VR 8631, remaining change +13 |
| hardname | 388 s | Place 1, final VR 8644 |
| easyname | 330 s | Koopa Troopa Beach course/roster screen |
| easyname | 480 s | SNES Choco Island 1 course/roster screen |
| hardname | 330 s | Dandelion Depths, lower map-label position |
| hardname | 60 s | Gameplay, not a results or course screen |

At hardname 389 s, the first-place face overlaps a panel highlight. Its small white
glint can OCR as `4`; the reader rejects this ambiguous frame rather than treating
a partial colored numeral as white text.

The FFmpeg sample checks the fourth-place italic glyph after a different
resampler. Its OCR confidence is lower despite a complete, readable shape;
rank acceptance still requires the highlighted panel, aligned table, sufficient
white glyph pixels, a bounded integer and downstream temporal agreement.

Additional final-result regressions cover easyname at 380 s, rank 8 / VR 8763;
945 s, rank 15 / VR 8752; 1330 s, rank 11 / VR 8788; and 1455 s, rank 21 /
VR 8737. The hardname 235.5 s FFmpeg-resampled table is rank 21 / VR 8566.
Whole-word OCR can collapse `11` into `1` and `21` into `2`. The reader checks
the number of separate glyphs and can read each complete glyph independently;
it never fills in a missing tens digit from the player's row location.
