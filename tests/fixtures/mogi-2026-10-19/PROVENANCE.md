> This derived repository omits the gameplay images described below. These are historical provenance notes; image-dependent tests must skip when local fixtures are absent.

# Mogi capture regression stills

Source: user-provided local OBS recording `2026-10-19_19-02-29.mkv`, referenced in GitLab issue #7. The original recording is not included or modified.

- `finishing-burnout.webp`: recording timestamp 2702.000 seconds, Dry Bones Burnout finishing table. Decoded with OpenCV, resized to the reader's 2048×1152 profile and encoded as WebP quality 90. The local player's bronze third-place digit remains colored against the gold highlight. Regression tests prove this digit without lowering the rank-match threshold and require all twelve finishing positions before offering review.

This intentional still-image fixture contains in-game names and the recorded scoreboard. It supplies phase/rank evidence, not verified identity mappings or game cumulative scores for scoring.

- `standings-railings.webp`: recording timestamp 1889.000 seconds, Whistlestop Summit results with consumed points over a track-railings background. Same OpenCV resize and WebP quality 90 processing as above. Railings previously passed the team-roster layout guard, clearing the current race association. Tests reject this false roster and retain course/results association. This reproduces the reset mechanism, not proof that this exact frame caused the user's earlier split entries.

- `prerace-rainbow.webp`: recording timestamp 2178.000 seconds, Rainbow Road roster/map. Same OpenCV resize and WebP quality 90 processing. Its label is above the usual course crop; the fallback crop is restricted to validated roster screens and known course names.

- `gameplay-ruins.webp`: recording timestamp 1417.700 seconds, Great ? Block Ruins gameplay before its results at about 1440 seconds. Same OpenCV resize and WebP quality 90 processing. The old team-roster guard falsely accepted this scene, reproducing a course/results association reset within an affected race's recording window. Horizontal panel continuity rejects it without changing race ownership rules.

- `partial-ghost.webp`: recording timestamp 737.300 seconds, SNES Ghost Valley 2 results table entering the screen. Names and +points suggest a table, but all twelve finishing ranks cannot be proven. Evidence-only detection must not create result rows or call neural name recognition.
- `finishing-ghost.webp`: recording timestamp 737.800 seconds, complete SNES Ghost Valley 2 finishing table. All strict phase/rank gates pass. Neural names are mocked in tests; the fixture does not verify identities.
- `points-consumed-ghost.webp`: recording timestamp 740.000 seconds, the same table after the +points column disappears. It can be retained as suspected evidence with an unverified/cumulative warning, never accepted as finishing rows.

The three Ghost Valley stills use the same OpenCV resize and WebP quality 90 processing as above. Read-only source sampling found strict finishing acceptance at 737.5 through 739.0 seconds, rejection at 737.4 and 739.1 seconds. These are sampled bounds, not continuous-frame proof or a diagnosis of the live OBS miss.

- `partial-wario.webp`: recording timestamp 1202.300 seconds, Wario's Shipyard results animation showing ranks 3–12 before the complete table arrives. Rejecting the first unproven rank must stop expensive row OCR while retaining evidence-only diagnostics.
- `finishing-wario.webp`: recording timestamp 1203.500 seconds, complete Wario's Shipyard table with the local player highlighted in first place. Its gold rank numeral shares the panel hue but has lower saturation. The alternate bright, lower-saturation mask retains the glyph and still requires the unchanged 0.90 shape match. All other rank identities and a solid gold panel remain rejected. Same OpenCV resize and WebP quality 90 processing as above; source recording untouched.
