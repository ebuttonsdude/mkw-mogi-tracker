"""On-demand timeline marker exports from recording-relative race timestamps."""

_FPS = 60  # Exact, non-drop-frame Resolve timeline rate (not 60000/1001).
_TIMELINE_START = 3600 * _FPS


def _timecode(frames):
    seconds, frame = divmod(frames, _FPS)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02}:{frame:02}"


def format_resolve_edl(races):
    """Render all rows in order or raise ValueError before returning a file."""
    blocks = ["TITLE: Race chapters\r\nFCM: NON-DROP FRAME"]
    for number, race in enumerate(races, 1):
        name = race.get("course")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Race {number}: enter a course name before exporting EDL markers.")
        if "|" in name or any(ord(c) < 32 or c in "\x7f\x85\u2028\u2029" for c in name):
            raise ValueError(f"Race {number}: remove line breaks, control characters and pipes (|) from the course name.")
        ms = race.get("offset_ms")
        if type(ms) is not int or ms < 0:
            raise ValueError(f"Race {number} ({name}): enter a non-negative recording timestamp in the timing editor.")
        start = _TIMELINE_START + (ms * _FPS + 500) // 1000
        if start + 1 >= 100 * 3600 * _FPS:
            raise ValueError(f"Race {number} ({name}): check the timestamp; its EDL timecode exceeds 99 hours.")
        marker_in, marker_out = _timecode(start), _timecode(start + 1)
        blocks.append(f"{number:03}  001      V     C        {marker_in} {marker_out} {marker_in} {marker_out}\r\n"
                      f" |C:ResolveColorBlue |M:{name} |D:1")
    if len(blocks) == 1:
        raise ValueError("No races in this recording. Assign a race and a timestamp before exporting EDL markers.")
    return ("\r\n\r\n".join(blocks) + "\r\n").encode("utf-8")
