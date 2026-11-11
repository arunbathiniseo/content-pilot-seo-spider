"""Word counts, readability and SERP pixel widths."""
from __future__ import annotations

import functools
import re
from pathlib import Path

try:
    from PIL import ImageFont
except Exception:  # pragma: no cover
    ImageFont = None

# textstat drags in nltk and scipy (~1.8 s): imported on first use, or by warm() from a background thread at startup
_textstat: object | None = None
_textstat_tried = False


def _ts():
    global _textstat, _textstat_tried
    if not _textstat_tried:
        _textstat_tried = True
        try:
            import textstat  # noqa: WPS433
            _textstat = textstat
        except Exception:  # pragma: no cover
            _textstat = None
    return _textstat


def warm() -> None:
    """Load the heavy optional libraries now (called from a background thread after the server is up)."""
    _ts()

_WORD_RE = re.compile(r"[A-Za-z0-9\u00C0-\u024F'’-]+")
_SENT_RE = re.compile(r"[.!?]+(?:\s|$)")
_LOREM_RE = re.compile(r"lorem ipsum", re.I)

# Google SERP: title ~20px Arial, description ~14px Arial (desktop).
_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
]


@functools.lru_cache(maxsize=4)
def _font(size: int):
    if ImageFont is None:
        return None
    for p in _FONT_CANDIDATES:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return None


def pixel_width(text: str, size: int) -> int:
    if not text:
        return 0
    font = _font(size)
    if font is None:
        return int(len(text) * size * 0.5)
    try:
        return int(font.getlength(text))
    except Exception:
        bbox = font.getbbox(text)
        return int(bbox[2] - bbox[0])


def title_px(text: str) -> int:
    return pixel_width(text, 20)


def desc_px(text: str) -> int:
    return pixel_width(text, 14)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def sentence_count(text: str) -> int:
    if not text:
        return 0
    n = len(_SENT_RE.findall(text))
    return max(n, 1 if text.strip() else 0)


def readability(text: str) -> tuple[float | None, str]:
    """Flesch reading ease score and the Screaming Frog band name."""
    ts = _ts() if text and word_count(text) >= 10 else None
    if ts is None:
        return None, ""
    try:
        score = float(ts.flesch_reading_ease(text))
    except Exception:
        return None, ""
    score = max(-100.0, min(121.0, score))
    if score >= 90:
        band = "Very Easy"
    elif score >= 80:
        band = "Easy"
    elif score >= 70:
        band = "Fairly Easy"
    elif score >= 60:
        band = "Standard"
    elif score >= 50:
        band = "Fairly Difficult"
    elif score >= 30:
        band = "Difficult"
    else:
        band = "Very Difficult"
    return round(score, 2), band


def has_lorem_ipsum(text: str) -> bool:
    return bool(_LOREM_RE.search(text or ""))


_SOFT404_RE = re.compile(
    r"\b(page (not|cannot be) found|404 error|error 404|not found|no longer (exists|available)|"
    r"doesn'?t exist|oops!? ?(something went wrong|that page)|the page you (requested|are looking for))\b",
    re.I,
)


def looks_like_soft_404(title: str, h1: str, text: str, word_count_: int) -> bool:
    probe = " ".join(x for x in (title or "", h1 or "") if x)
    if _SOFT404_RE.search(probe):
        return True
    if word_count_ < 150 and _SOFT404_RE.search((text or "")[:600]):
        return True
    return False
