"""Review-assisted OCR for an unobstructed 16:9 Mario Kart World feed.

Coordinates describe the supplied full-frame profile. OCR names are candidates,
never proof of identity. Scoring must follow review in the application.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .courses import match_course
from .name_engines import GENERATIVE_NAME_ENGINES
from .tesseract import tesseract_executable


@lru_cache(maxsize=1)
def _rank_templates():
    profile = json.loads((Path(__file__).parent / 'profiles/rank_templates.json').read_text())
    return {int(rank): cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
                                   cv2.IMREAD_GRAYSCALE)
            for rank, encoded in profile['masks'].items()}


class ResultReader:
    def __init__(self, tessdata_path='.local/tessdata', name_reader=None):
        self.tessdata_path = Path(tessdata_path)
        self.name_reader = name_reader
        self._active_name_engine = name_reader.engine if name_reader else "tesseract"
        self._name_engine_status = "selected" if name_reader else "ready"
        self._name_engine_error = None

    @property
    def recognition_status(self):
        return {"name_engine": self._active_name_engine,
                "selected_name_engine": self.name_reader.engine if self.name_reader else "tesseract",
                "name_engine_status": self._name_engine_status,
                "name_engine_error": self._name_engine_error}

    def close(self):
        if self.name_reader:
            self.name_reader.close()

    def warmup(self):
        if self.name_reader:
            row = np.full((96, 320, 3), 255, dtype=np.uint8)
            cv2.putText(row, 'Ready', (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 2)
            self._name_engine_status = "loading"
            try:
                if self.name_reader.engine == 'external-vision':
                    self.name_reader.warmup()
                else:
                    self.name_reader.recognize([row])
            except Exception as error:
                self._name_engine_status = "error"
                self._name_engine_error = str(error)
                raise
            self._active_name_engine = self.name_reader.engine
            self._name_engine_status = "ready"
            self._name_engine_error = None

    @staticmethod
    def _frame(frame):
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError('Expected a BGR image')
        height, width = frame.shape[:2]
        if abs(width / height - 16 / 9) > .04:
            raise ValueError('Recognition requires an uncropped 16:9 game image')
        return cv2.resize(frame, (2048, 1152))

    def _ocr(self, image, *, language='eng+jpn', psm=6, whitelist=None):
        command = [tesseract_executable() or 'tesseract', 'stdin', 'stdout', '--tessdata-dir',
                   str(self.tessdata_path), '-l', language, '--psm', str(psm)]
        if whitelist:
            command += ['-c', f'tessedit_char_whitelist={whitelist}']
        command += ['-c', 'tessedit_create_tsv=1']
        encoded = cv2.imencode('.png', image)[1].tobytes()
        result = subprocess.run(command, input=encoded, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f'Tesseract failed: {result.stderr.decode(errors="replace").strip()}')
        words = []
        # Tesseract emits unquoted TSV. A quote in a player's name must not
        # consume subsequent lines as if this were quoted CSV.
        for row in csv.DictReader(io.StringIO(result.stdout.decode()), delimiter='\t', quoting=csv.QUOTE_NONE):
            if row['level'] == '5' and row['text'].strip():
                words.append({'text': row['text'].strip(), 'confidence': max(0, float(row['conf'])) / 100,
                              'top': int(row['top']), 'height': int(row['height'])})
        return words

    @staticmethod
    def _text_mask(crop, *, row_height=None):
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        foreground = cv2.inRange(hsv, (0, 0, 175), (180, 110, 255))
        if row_height:
            # Team panels use dark ink on saturated red/blue/green/yellow.
            # Keep white ink for the local highlighted player, and isolate
            # each row so panel edges do not turn into OCR punctuation.
            for top in range(0, crop.shape[0], row_height):
                row = hsv[top:top + 70]
                existing = foreground[top:top + len(row)]
                if np.median(row[:, :, 1]) > 110 and (existing > 0).mean() < .025:
                    dark = row[:, :, 2] < np.median(row[:, :, 2]) * .80
                    foreground[top:top + len(row)] |= dark.astype(np.uint8) * 255
                    foreground[top:top + 12] = 0
                    foreground[top + 65:top + row_height] = 0
        return 255 - foreground

    @staticmethod
    def _rank_matches(crop, expected):
        """Prove a profile rank by glyph shape when Tesseract misses its font."""
        template = _rank_templates().get(expected)
        if template is None or crop.shape[:2] != template.shape:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        gold = (hue > 15) & (hue < 40) & (saturation > 150) & (value > 160)
        gold_background = gold.mean() > .35
        # Highlighted podium digits retain their colored faces. Remove the gold
        # panel, not their colored ink; other ranks have white foreground.
        if expected > 3:
            mask = (value > 180) & (saturation < 85)
        else:
            mask = (value > 175) & ~gold if gold_background else value > 175
        candidate = mask.astype(np.float32)
        target = (template > 0).astype(np.float32)
        candidates = [candidate]
        if gold_background and expected <= 3:
            # A gold first-place face shares the highlighted panel's hue but is
            # less saturated. Hue removal alone erases most of that numeral.
            candidates.append(((value > 175) & (saturation < 210)).astype(np.float32))
        if np.median(saturation) > 110:
            # Team ranks have the same geometry but may be dark instead of
            # white. Otsu thresholds adapt to each team's panel brightness.
            for channel in (value, *cv2.split(crop)):
                binary = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
                for binary_mask in (binary, 255 - binary):
                    alternative = (binary_mask > 0).astype(np.float32)
                    alternative[:10] = 0
                    alternative[64:] = 0
                    candidates.append(alternative)
        for candidate in candidates:
            padded = cv2.copyMakeBorder(candidate, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=0)
            intersection = float(cv2.matchTemplate(padded, target, cv2.TM_CCORR).max())
            denominator = float(candidate.sum() + target.sum())
            # Fixed-size Dice overlap preserves glyph size and rejects arbitrary
            # bright content. A small translation allows resampling differences.
            if denominator > 0 and 2 * intersection / denominator >= .90:
                return True
        return False

    def _rejected_result(self, frame, grouped, phase, reason, plus_rows=()):
        result = {'phase': phase, 'rows': [], 'needs_review': True, 'reasons': [reason]}
        named_rows = sum(bool(words) for words in grouped)
        if named_rows < 6:
            return result
        # Evidence only: count cheap glyph matches independently of where strict
        # recognition stopped. Never launch extra OCR for this diagnostic.
        rank_rows = sum(self._rank_matches(frame[45 + i * 82:115 + i * 82, 1191:1255], i + 1)
                        for i in range(12))
        if len(plus_rows) < 3 and (named_rows < 8 or rank_rows < 6):
            return result
        result.update(
            suspected_results=True,
            suspected_results_reason=(f'{reason}. Suspected table only; finishing order is unverified. '
                                      'The image may be partial or show reordered cumulative standings.'),
            suspected_results_details={'name_rows': named_rows, 'plus_rows': len(plus_rows),
                                       'rank_rows': rank_rows})
        return result

    def read(self, frame_bgr, *, allow_partial=False):
        frame = self._frame(frame_bgr)
        names = self._ocr(self._text_mask(frame[45:1025, 1357:1755], row_height=82))
        grouped = [[] for _ in range(12)]
        for word in names:
            index = round((word['top'] + word['height'] / 2 - 36) / 82)
            if 0 <= index < 12:
                grouped[index].append(word)
        # A stronger recognizer must still get a chance when a symbol-only or
        # Japanese name is unreadable. Position proof remains mandatory below.
        missing_baseline = not all(grouped)
        if missing_baseline and self.name_reader is None and not allow_partial:
            return self._rejected_result(frame, grouped, 'unknown', 'No complete results table detected')
        additions = self._ocr(255 - cv2.threshold(
            cv2.cvtColor(frame[45:1025, 1770:1855], cv2.COLOR_BGR2GRAY),
            170, 255, cv2.THRESH_BINARY)[1], language='eng', whitelist='+0123456789')
        plus_rows = {round((w['top'] + w['height'] / 2 - 36) / 82)
                     for w in additions if re.fullmatch(r'\+\d+', w['text'])}
        plus_rows &= set(range(12))
        # Reordered cumulative standings have no +column. Never use their rows
        # as finishing positions, even if all twelve players are present.
        if len(plus_rows) < 3:
            return self._rejected_result(frame, grouped, 'unknown' if missing_baseline else 'standings',
                                         'Finishing-order phase not proven by the +points column', plus_rows)
        rows = []
        reasons = ['Player names require visual verification before scoring']
        for index, words in enumerate(grouped):
            top = 45 + index * 82
            rank_crop = frame[top:top + 70, 1191:1255]
            expected = index + 1
            # The existing strict glyph proof is cheaper than twelve Tesseract
            # subprocesses. Keep exactly the same OCR fallback for other glyphs.
            if self._rank_matches(rank_crop, expected):
                rank_text = str(expected)
            else:
                # Brightest channel retains the colored podium digits.
                rank = self._ocr(255 - cv2.threshold(rank_crop.max(axis=2),
                                 175, 255, cv2.THRESH_BINARY)[1], language='eng', psm=7,
                                 whitelist='0123456789')
                rank_text = ''.join(w['text'] for w in rank)
            if rank_text != str(expected):
                matches = False
                if (expected <= 3
                        and np.median(cv2.cvtColor(rank_crop, cv2.COLOR_BGR2HSV)[:, :, 1]) > 110):
                    # Team podium digits have shaded colored faces. Their
                    # green channel (gold/bronze) or red channel (silver) keeps
                    # the complete glyph where a brightest-channel mask does not.
                    channel = rank_crop[:, :, 2 if expected == 2 else 1]
                    binary = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
                    alternate = self._ocr(binary, language='eng', psm=7, whitelist='0123456789')
                    matches = ''.join(w['text'] for w in alternate) == str(expected)
                if not matches and allow_partial:
                    # Only proven slots may supply names; missing slots are filled
                    # by the opt-in coordinator, never by ordinary recognition.
                    continue
                if not matches:
                    # One unproven rank already makes this frame ineligible.
                    # Sliding tables must not stall sampling for eleven more OCRs.
                    return self._rejected_result(
                        frame, grouped, 'unknown',
                        'Incomplete table: all 12 finishing positions must be visible and verified',
                        plus_rows)
                reasons.append(f'Row {expected} position OCR is {rank_text!r}; verify against frame')
            name = ' '.join(w['text'] for w in words)
            confidence = min((w['confidence'] for w in words), default=0)
            if not name:
                reasons.append(f'Row {expected} name is missing')
            rows.append({'position': expected, 'name': name, 'confidence': confidence})
        if not rows:
            return self._rejected_result(frame, grouped, 'unknown',
                                         'No finishing position proven', plus_rows)
        engine = 'tesseract'
        if self.name_reader:
            sy, sx = frame_bgr.shape[0] / 1152, frame_bgr.shape[1] / 2048
            crops = [frame_bgr[round((45 + i * 82) * sy):round((115 + i * 82) * sy),
                              round(1357 * sx):round(1755 * sx)]
                     for i in (row['position'] - 1 for row in rows)]
            try:
                if self._name_engine_status != "ready":
                    self._name_engine_status = "loading"
                recognized = self.name_reader.recognize(crops)
                for row, name in zip(rows, recognized):
                    row.update(name)
                    row['confidence_available'] = self.name_reader.engine not in GENERATIVE_NAME_ENGINES
                engine = self.name_reader.engine
                self._active_name_engine = engine
                self._name_engine_status = "ready"
                self._name_engine_error = None
                reasons.append(f'Names read by {engine}; verify Japanese, small kana, and symbols against the screenshot.')
            except Exception as error:
                self._active_name_engine = "tesseract"
                self._name_engine_status = "fallback"
                self._name_engine_error = str(error)
                reasons.append(f'Neural name recognition failed; baseline candidates shown for correction: {error}')
        return {'phase': 'finishing_results', 'rows': rows, 'name_engine': engine,
                'needs_review': True, 'reasons': reasons}

    def read_partial(self, frame_bgr, *, minimum_rows=0):
        """Read independently proven visible slots for manual review, never YOLO.

        Scrolling tables have no fixed first row. +points locate finishing-phase
        evidence; rank glyphs, not point values or row indices, identify slots.
        """
        frame = self._frame(frame_bgr)
        rejected = {'phase': 'unknown', 'rows': [], 'needs_review': True,
                    'reasons': ['No coherent partial finishing table proven']}
        # Cheap ink/spacing guard before OCR on ordinary gameplay frames. This
        # is only a rejection filter: every accepted slot still needs glyph proof.
        hsv = cv2.cvtColor(frame[:, 1191:1255], cv2.COLOR_BGR2HSV)
        ink = cv2.inRange(hsv, (0, 0, 175), (180, 110, 255))
        ys = np.flatnonzero((ink > 0).sum(axis=1) >= 5)
        runs = np.split(ys, np.where(np.diff(ys) > 3)[0] + 1)
        centers = [float((run[0] + run[-1]) / 2) for run in runs
                   if len(run) and 20 <= run[-1] - run[0] + 1 <= 55]
        if not any(sum(abs((other - center) / 82 - round((other - center) / 82)) < .15
                       for other in centers) >= 3 for center in centers):
            return rejected
        additions = self._ocr(255 - cv2.threshold(
            cv2.cvtColor(frame[:, 1770:1855], cv2.COLOR_BGR2GRAY),
            170, 255, cv2.THRESH_BINARY)[1], language='eng', whitelist='+0123456789')
        additions = [word for word in additions if re.fullmatch(r'\+\d+', word['text'])]
        if not 3 <= len(additions) <= 24:
            return rejected

        def prove(center):
            matches = {}
            for delta in range(-12, 13, 3):
                top = round(center - 36 + delta)
                if not 0 <= top <= frame.shape[0] - 70:
                    continue
                crop = frame[top:top + 70, 1191:1255]
                for rank in range(1, 13):
                    if self._rank_matches(crop, rank):
                        matches.setdefault(rank, []).append(top)
            if len(matches) != 1:
                return None
            rank, tops = next(iter(matches.items()))
            return rank, float(np.median(tops))

        anchors = [proof for word in additions
                   if (proof := prove(word['top'] + word['height'] / 2)) is not None]
        anchors.sort(key=lambda proof: proof[1])
        if len(anchors) < 3 or len({rank for rank, _ in anchors}) != len(anchors):
            return rejected
        if any(rank_b <= rank_a or abs(top_b - top_a - 82 * (rank_b - rank_a)) > 12
               for (rank_a, top_a), (rank_b, top_b) in zip(anchors, anchors[1:])):
            return rejected
        offset = float(np.median([top - (rank - 1) * 82 for rank, top in anchors]))
        slots = []
        for rank in range(1, 13):
            top = round(offset + (rank - 1) * 82)
            if not 0 <= top <= frame.shape[0] - 70:
                continue
            # Grid membership never supplies a missing rank. Match all glyphs
            # to reject ambiguity, without changing the strict match threshold.
            crop = frame[top:top + 70, 1191:1255]
            matches = [value for value in range(1, 13) if self._rank_matches(crop, value)]
            if matches == [rank]:
                slots.append((rank, top))
        if len(slots) < 3 or len(slots) <= minimum_rows:
            return rejected

        # Pack only proven rows for one baseline OCR, preserving slot alignment
        # even when a name or an entire intermediate rank is unreadable.
        strip = np.zeros((len(slots) * 82, 398, 3), dtype=np.uint8)
        for index, (_, top) in enumerate(slots):
            strip[index * 82:index * 82 + 70] = frame[top:top + 70, 1357:1755]
        grouped = [[] for _ in slots]
        for word in self._ocr(self._text_mask(strip, row_height=82)):
            index = round((word['top'] + word['height'] / 2 - 36) / 82)
            if 0 <= index < len(grouped):
                grouped[index].append(word)
        rows = [{'position': rank, 'name': ' '.join(word['text'] for word in words),
                 'confidence': min((word['confidence'] for word in words), default=0)}
                for (rank, _), words in zip(slots, grouped)]
        reasons = ['Partial finishing table: unknown positions remain blank; manual confirmation required',
                   'Player names require visual verification before scoring']
        engine = 'tesseract'
        if self.name_reader:
            sy, sx = frame_bgr.shape[0] / 1152, frame_bgr.shape[1] / 2048
            crops = [frame_bgr[round(top * sy):round((top + 70) * sy),
                              round(1357 * sx):round(1755 * sx)] for _, top in slots]
            try:
                self._name_engine_status = 'loading'
                recognized = self.name_reader.recognize(crops)
                if len(recognized) != len(rows):
                    raise ValueError('Name reader returned a different number of rows')
                # Build atomically so malformed responses leave all baseline
                # candidates intact. A name engine cannot change proven ranks.
                updated = [dict(row, name=name.get('name', ''),
                                confidence=name.get('confidence', 0),
                                confidence_available=self.name_reader.engine not in GENERATIVE_NAME_ENGINES)
                           for row, name in zip(rows, recognized)]
                rows = updated
                engine = self.name_reader.engine
                self._active_name_engine = engine
                self._name_engine_status = 'ready'
                self._name_engine_error = None
            except Exception as error:
                self._active_name_engine = 'tesseract'
                self._name_engine_status = 'fallback'
                self._name_engine_error = str(error)
                reasons.append(f'Neural name recognition failed; baseline candidates shown for correction: {error}')
        reasons.extend(f'Row {row["position"]} name is missing' for row in rows if not row['name'])
        return {'phase': 'partial_results', 'rows': rows, 'name_engine': engine,
                'needs_review': True, 'reasons': reasons}

    def is_prerace_layout(self, frame_bgr):
        frame = self._frame(frame_bgr)
        # Two roster columns contain six grey panels against a navy background.
        # This guard prevents arbitrary map/gameplay text from opening a race.
        neutral_columns = 0
        for x in (420, 950):
            panels = [frame[y - 8:y + 8, x - 8:x + 8].mean(axis=(0, 1))
                      for y in (130, 212, 294, 376, 458, 540)]
            neutral_columns += sum(float(p.max() - p.min()) < 6 and 30 < p.mean() < 150
                                   for p in panels) >= 5
        if neutral_columns == 2:
            return True
        # In-game teams reserve twelve slots per column, including unused
        # translucent slots. Require repeated flat colored panels AND their
        # regularly spaced separator gaps, not just a particular hue.
        for x in (420, 950):
            flat = colored = separated = continuous = 0
            for y in range(130, 1033, 82):
                patch = frame[y - 8:y + 8, x - 8:x + 8]
                panel = patch.mean(axis=(0, 1))
                gap = frame[y + 36:y + 40, x - 8:x + 8].mean(axis=(0, 1))
                beside = frame[y - 8:y + 8, x + 24:x + 40].mean(axis=(0, 1))
                flat += float(patch.std(axis=(0, 1)).max()) < 12
                colored += float(panel.max() - panel.min()) > 20
                separated += float(np.linalg.norm(panel - gap)) > 15
                # A roster panel continues horizontally. Track railings behind
                # results can otherwise mimic flat colored slots and gaps.
                continuous += float(np.linalg.norm(panel - beside)) < 20
            if flat < 10 or colored < 8 or separated < 9 or continuous < 10:
                return False
        return True

    def read_prerace(self, frame_bgr):
        if not self.is_prerace_layout(frame_bgr):
            return None
        frame = self._frame(frame_bgr)
        # Rainbow Road's map label sits above the usual profile location.
        # Try that crop only after the usual one fails, and require a known
        # course there so map artwork cannot become an invented course name.
        for top, bottom in ((375, 430), (340, 390)):
            course_mask = self._text_mask(frame[top:bottom, 1420:1800])
            # Bright map artwork outside the dark label can touch the crop edges
            # and make OCR merge it with the course text (notably Sky-High Sundae).
            count, labels, stats, _ = cv2.connectedComponentsWithStats(255 - course_mask)
            height, width = course_mask.shape
            for component in range(1, count):
                x, y, w, h, _ = stats[component]
                if x == 0 or y == 0 or x + w == width or y + h == height:
                    course_mask[labels == component] = 255
            words = self._ocr(course_mask, language='eng', psm=7)
            words = [word for word in words if 10 <= word['height'] <= 35
                     and any(char.isalnum() for char in word['text'])]
            course = ' '.join(word['text'] for word in words)
            confidence = min((word['confidence'] for word in words), default=0)
            if len(course) > 5 and confidence > .35:
                canonical = match_course(course)
                if top == 375 or canonical:
                    return {'course': canonical or f'Unknown course: {course}', 'confidence': confidence,
                            'observed_course': course, 'matched_course': canonical is not None}
        return None
