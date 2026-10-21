"""Markdown and HTML helpers shared by the stages and the reporter. Standard library only.

GitHub renders a pull request comment as Markdown with a small, sanitised subset of HTML. Two things here matter:
- code_block picks a fence longer than any backtick run inside the text, so program output can never end the
  block early (and is shown as a real, monospaced code block);
- html_table makes full-width tables (GitHub keeps <table width="100%">), which plain Markdown tables cannot do.
"""
import html
import re

FENCE_LINE = re.compile(r"^ {0,3}(`{3,})(.*)$")


def esc(text):
    """Escape text for use inside HTML elements."""
    return html.escape(str(text), quote=False)


def code_block(text, lang=""):
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text.rstrip(chr(10))}\n{fence}"


def html_table(headers, rows, widths=None, aligns=None):
    """Full-width table. Cells are HTML (escape text with esc); widths/aligns are optional per-column lists."""
    widths, aligns = widths or [None] * len(headers), aligns or [None] * len(headers)

    def attrs(index, with_width):
        out = ""
        if with_width and widths[index]:
            out += f' width="{widths[index]}"'
        if aligns[index]:
            out += f' align="{aligns[index]}"'
        return out

    head = "".join(f"<th{attrs(i, True)}>{cell}</th>" for i, cell in enumerate(headers))
    body = "".join("<tr>" + "".join(f"<td{attrs(i, False)}>{cell}</td>" for i, cell in enumerate(row)) + "</tr>"
                   for row in rows)
    return f'<table width="100%"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _walk_fences(text):
    """Yield (line, in_fence_before_line, fence_length_if_open) for each line."""
    open_length = 0
    for line in text.split("\n"):
        yield line, open_length
        match = FENCE_LINE.match(line)
        if not match:
            continue
        if not open_length:
            open_length = len(match.group(1))
        elif len(match.group(1)) >= open_length and not match.group(2).strip():
            open_length = 0


def clip_markdown(text, limit, note="… (cut, see the run artifact)"):
    """Cut at a line boundary below limit characters, and close a code block left open (by the cut or by the text
    itself), so an unbalanced fence can never swallow the rest of the comment."""
    kept, size, open_length, cut = [], 0, 0, False
    for line, state in _walk_fences(text):
        if size + len(line) + 1 > limit:
            cut = True
            break
        kept.append(line)
        size += len(line) + 1
        open_length = state
        match = FENCE_LINE.match(line)
        if match and not state:
            open_length = len(match.group(1))
        elif match and len(match.group(1)) >= state and not match.group(2).strip():
            open_length = 0
    out = "\n".join(kept)
    if open_length:
        out += "\n" + "`" * open_length
    return out + "\n\n" + note if cut else out


def neutralize_mentions(text):
    """Defuse @mentions outside code blocks (a mention in a comment notifies someone)."""
    out = []
    for line, state in _walk_fences(text):
        out.append(line if state or FENCE_LINE.match(line) else re.sub(r"@(?=\w)", "@​", line))
    return "\n".join(out)


def square_bar(base, candidate, capacity, width=32):
    """Squares for one memory: blue = used in both, orange = added by the PR, green = freed, black = free.
    Any change shows as at least one square. Returns (squares, bytes_per_square)."""
    unit = capacity / width
    base_squares, new_squares = round(base / unit), round(candidate / unit)
    if candidate > base and new_squares == base_squares:
        new_squares += 1
    elif candidate < base and new_squares == base_squares:
        base_squares += 1
    base_squares, new_squares = min(width, base_squares), min(width, new_squares)
    common = min(base_squares, new_squares)
    grown, freed = new_squares - common, base_squares - common
    free = width - common - grown - freed
    return "🟦" * common + "🟧" * grown + "🟩" * freed + "⬛" * free, unit


def safe_prose(text):
    """Untrusted prose (model output, PR text) as comment-safe HTML: `code` spans become <code>, everything else is
    escaped, mentions and autolinks are defused. No other markup survives."""
    out = []
    for index, part in enumerate(re.split(r"`([^`\n]+)`", text)):
        if index % 2:
            out.append(f"<code>{esc(part)}</code>")
            continue
        part = esc(part)
        part = re.sub(r"@(?=\w)", "@\u200b", part)
        out.append(re.sub(r"(https?)://", lambda m: m.group(1) + "\u200b://", part))
    return "".join(out)
