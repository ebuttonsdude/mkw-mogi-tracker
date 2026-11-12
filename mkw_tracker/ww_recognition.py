"""CPU-only observations of the highlighted local player in worldwide results.

This reader does not identify names or decide when a race is complete. The
coordinator must check repeated observations and the VR counting animation.
"""

from __future__ import annotations

import re

from .courses import match_course


def _runs(indices, gap=1):
    import numpy as np

    return [part for part in np.split(indices, np.where(np.diff(indices) > gap)[0] + 1) if len(part)]


def _white(crop):
    import cv2

    return cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), (0, 0, 170), (180, 115, 255))


def _ink(crop):
    import cv2

    mask = _white(crop)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    height, width = mask.shape
    for component in range(1, count):
        x, y, w, h, area = stats[component]
        if area < 3 or x == 0 or y == 0 or x + w == width or y + h == height:
            mask[labels == component] = 0
    return mask


class WWReader:
    """Read WW digits without starting any optional player-name model."""

    def __init__(self, name_engine='auto', *, tessdata_path='.local/tessdata'):
        from .recognition import ResultReader

        self._ocr_reader = ResultReader(tessdata_path=tessdata_path)

    @property
    def recognition_status(self):
        return {'name_engine': 'ww-digits', 'selected_name_engine': 'ww-digits',
                'name_engine_status': 'ready', 'name_engine_error': None,
                'recognition_description': 'WW highlighted-player digits, CPU Tesseract; no name recognition'}

    def warmup(self):
        pass

    def close(self):
        self._ocr_reader.close()

    @staticmethod
    def _frame(frame):
        import cv2

        # Keep the same uncropped 16:9 input contract as Mogi, with WW geometry.
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError('Expected a BGR image')
        height, width = frame.shape[:2]
        if not height or abs(width / height - 16 / 9) > .04:
            raise ValueError('Recognition requires an uncropped 16:9 game image')
        return cv2.resize(frame, (1280, 720))

    @staticmethod
    def _table_rows(frame):
        import numpy as np

        mask = _white(frame[15:705, 1152:1237])
        rows = []
        for run in _runs(np.flatnonzero((mask > 0).sum(axis=1) >= 5), gap=3):
            if not 11 <= run[-1] - run[0] + 1 <= 35:
                continue
            center = int((run[0] + run[-1]) // 2) + 15
            # A second independently aligned rank column prevents bright map
            # artwork in the rating column from being a results-table signal.
            rank = frame[max(0, center - 18):center + 19, 712:757]
            if (_white(rank) > 0).sum() >= 25 or WWReader._podium_rank(rank) is not None:
                rows.append(center)
        return rows

    def is_results_layout(self, frame):
        rows = self._table_rows(self._frame(frame))
        # Rows scroll, so their absolute Y coordinate is not fixed. The local
        # player can be absent or pinned below the top page without ending it.
        return any(43 <= lower - upper <= 59 for upper, lower in zip(rows, rows[1:]))

    @staticmethod
    def _highlight(frame):
        import cv2
        import numpy as np

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gold = cv2.inRange(hsv, (15, 140, 160), (40, 255, 255)) > 0
        candidates = []
        for run in _runs(np.flatnonzero(gold[:, 1000:1080].mean(axis=1) > .90)):
            height = run[-1] - run[0] + 1
            if not 24 <= height <= 54 or run[0] < 18 or run[-1] > 701:
                continue
            center = int(round((run[0] + run[-1]) / 2))
            # Require one continuous, table-width highlighted panel, not a
            # yellow number, minimap icon, finish banner, or track decoration.
            if gold[center - 8:center + 9, 685:1235].mean() < .55:
                continue
            candidates.append(center)
        return candidates[0] if len(candidates) == 1 else None

    def _digits(self, mask, *, signed=False, scale=1, minimum_confidence=None, psm=7):
        import cv2

        image = cv2.resize(255 - mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        image = cv2.copyMakeBorder(image, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
        words = self._ocr_reader._ocr(image, language='eng', psm=psm,
                                      whitelist='+-0123456789' if signed else '0123456789')
        minimum = (.20 if signed else .50) if minimum_confidence is None else minimum_confidence
        if not words or min(word['confidence'] for word in words) < minimum:
            return None
        text = ''.join(word['text'] for word in words)
        pattern = r'[+-]\d{1,4}' if signed else r'\d{1,5}'
        return int(text) if re.fullmatch(pattern, text) else None

    def _rank_number(self, mask):
        import cv2
        import numpy as np

        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        glyphs = sorted((tuple(row), index) for index, row in enumerate(stats[1:], 1)
                        if row[4] >= 35 and row[3] >= 12)
        if not 1 <= len(glyphs) <= 2:
            return None
        value = self._template_rank(mask)
        if value is None:
            value = self._digits(mask, minimum_confidence=.25)
        if value is not None and len(str(value)) == len(glyphs):
            return value
        # Whole-word OCR can collapse '11' to '1' or '21' to '2'. Each full
        # separated glyph is independent evidence, not a guessed missing digit.
        digits = []
        for (x, y, width, height, _), index in glyphs:
            glyph = (labels[y:y + height, x:x + width] == index).astype(np.uint8) * 255
            digit = self._digits(glyph, minimum_confidence=.65, psm=10)
            if digit is None or not 0 <= digit <= 9:
                return None
            digits.append(str(digit))
        return int(''.join(digits)) if digits[0] != '0' else None

    def _rating(self, crop):
        import cv2

        mask = _ink(crop)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        glyphs = sum(10 <= height <= 32 and 4 <= width <= 25 and area >= 35
                     for _, _, width, height, area in stats[1:])
        if not 1 <= glyphs <= 5:
            return None
        for scale in (2, 1):
            value = self._digits(mask, scale=scale)
            if value is not None and not 1 <= value <= 99999:
                return None
            # Tesseract sometimes inserts an extra digit inside an italic
            # glyph. The separated rating faces independently prove length.
            if value is not None and len(str(value)) == glyphs:
                return value
        return None

    @staticmethod
    def _podium_rank(crop):
        import cv2
        import numpy as np
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # Colored podium faces are not white text. Compare their shape with
        # existing rank glyphs instead of guessing from their table row index.
        mask = ((hsv[:, :, 2] > 175) & (hsv[:, :, 1] < 210)).astype(np.uint8) * 255
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for component in range(1, count):
            if stats[component, cv2.CC_STAT_AREA] < 8:
                mask[labels == component] = 0
        return WWReader._template_rank(mask, maximum=3, minimum=.90)

    @staticmethod
    def _template_rank(mask, *, maximum=12, minimum=.88):
        import cv2
        from .recognition import _rank_templates

        x, y, width, height = cv2.boundingRect(mask)
        if not width or not height:
            return None
        candidate = mask[y:y + height, x:x + width] > 0
        scores = []
        for rank, template in _rank_templates().items():
            if rank > maximum:
                continue
            tx, ty, tw, th = cv2.boundingRect(template)
            if abs(width / height - tw / th) > .15:
                continue
            target = cv2.resize(template[ty:ty + th, tx:tx + tw], (width, height),
                                interpolation=cv2.INTER_NEAREST) > 0
            score = 2 * (candidate & target).sum() / (candidate.sum() + target.sum())
            scores.append((float(score), rank))
        scores.sort(reverse=True)
        if scores and scores[0][0] >= minimum and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .08):
            return scores[0][1]
        return None

    def read_results(self, frame):
        frame = self._frame(frame)
        rows = self._table_rows(frame)
        if not any(43 <= lower - upper <= 59 for upper, lower in zip(rows, rows[1:])):
            return None
        center = self._highlight(frame)
        if center is None:
            return None
        top, bottom = max(0, center - 22), min(720, center + 22)
        rank_crop = frame[top:bottom, 712:757]
        position = self._podium_rank(rank_crop)
        if position is None:
            rank_ink = _ink(rank_crop)
            # A colored podium numeral can leave only a small white glint.
            # Tesseract can confidently call that fragment a different digit.
            if (rank_ink > 0).sum() < 80:
                return None
            position = self._rank_number(rank_ink)
        if position is None or not 1 <= position <= 24:
            return None
        vr = self._rating(frame[top:bottom, 1148:1240])
        if vr is None or not 1 <= vr <= 99999:
            return None
        delta_mask = _ink(frame[top:bottom, 1088:1148])
        if (delta_mask > 0).sum() >= 12:
            delta = self._digits(delta_mask, signed=True)
            # Failed OCR is not evidence that the counting animation finished.
            if delta is None:
                return None
            phase = 'change'
        else:
            delta, phase = None, 'final'
        return {'position': position, 'vr': vr, 'delta': delta, 'phase': phase,
                'lobby_size': None}

    def is_prerace_layout(self, frame):
        return self._ocr_reader.is_prerace_layout(frame)

    def read_prerace(self, frame):
        if not self.is_prerace_layout(frame):
            return None
        frame = self._ocr_reader._frame(frame)
        # WW's map pans vertically; the label is not always in Mogi's upper
        # crop. Restrict reads to the map and accept only the course vocabulary.
        mask = self._ocr_reader._text_mask(frame[250:1050, 1320:1940])
        words = self._ocr_reader._ocr(mask, language='eng', psm=11)
        lines = []
        for word in words:
            if not 10 <= word['height'] <= 60 or not any(char.isalnum() for char in word['text']):
                continue
            center = word['top'] + word['height']
            line = next((line for line in lines if abs(line[0] - center) <= 12), None)
            if line is None:
                line = [center, []]
                lines.append(line)
            line[1].append(word)
        courses = set()
        for _, line in lines:
            if sum(word['confidence'] for word in line) / len(line) < .45:
                continue
            course = match_course(' '.join(word['text'] for word in line))
            if course:
                courses.add(course)
        if len(courses) == 1:
            return {'course': courses.pop(), 'lobby_size': None}
        return None
