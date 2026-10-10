"""Count logical automatic frames across alternate transcript representations."""
import hashlib, re
from . import LABEL

def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)

def observe(session, row, source, at):
    from .policy import valid_message
    frames, unknown = {}, 0
    for text in strings(row):
        if re.search(re.escape(LABEL) + r'(?:[0-9]{3,}/|[0-9]+/[0-9]{3,})', text):
            session.parse_limited = True
            if len(session.parse_notes) < 10:
                session.parse_notes.append(f'line {session.total_lines}: skipped malformed marker counters')
            continue
        counts, accepted = {}, 0
        for match in re.finditer(re.escape(LABEL) + r'[^\r\n]*\n[^\r\n]*\n[^\r\n]*', text):
            if valid_message(match[0]):
                key = hashlib.sha256(match[0].encode()).hexdigest()
                counts[key] = counts.get(key, 0) + 1
                accepted += 1
        for key, count in counts.items():
            frames[key] = max(frames.get(key, 0), count)
        unknown = max(unknown, text.count(LABEL)-accepted)
    # Preserve repeated occurrences in one source. Alternate sources represent
    # the same logical frames; use the largest occurrence count, never their sum.
    for key, count in frames.items():
        sources = session.marker_sources.setdefault(key, {})
        sources.setdefault(source, []).extend([at] * count)
    session.unmatched_marker_stamps.extend([at] * unknown)
    stamps = list(session.unmatched_marker_stamps)
    for sources in session.marker_sources.values():
        for index in range(max(map(len, sources.values()))):
            # A later duplicate cannot prematurely expire a counted occurrence.
            stamps.append(max(values[index] for values in sources.values() if len(values) > index))
    session.auto_stamps = stamps
    session.auto_total = len(stamps)
