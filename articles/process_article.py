#!/usr/bin/env python3
"""
process_article.py
Turns an article you emailed to your +podcast address into a podcast episode.

Run by .github/workflows/generate-article.yml. It:
  1. cleans up the email text (forwarding headers, links, footnote numbers),
     or, if you only sent a link, downloads the article from that link;
  2. reads it aloud with the Kokoro voice;
  3. saves an MP3 in articles/audio/ and adds it to articles/feed.xml.
"""

import json
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(HERE, "audio")
FEED_FILE = os.path.join(HERE, "feed.xml")

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "fredasboss")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "money-stuff-podcast")
BASE_URL = f"https://{GITHUB_USERNAME}.github.io/{GITHUB_REPO}/articles"

VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
SPEED = float(os.environ.get("KOKORO_SPEED", "1.0"))
KEEP = int(os.environ.get("KEEP_EPISODES", "40"))
MODEL_DIR = os.environ.get("KOKORO_MODEL_DIR", "models")
MAX_CHARS = 120_000  # roughly 2 hours of audio

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"


# --------------------------------------------------------------------------
# 1. Getting and cleaning the text
# --------------------------------------------------------------------------

FWD_MARKERS = [
    r"^-{3,}\s*Forwarded message\s*-{3,}",
    r"^Begin forwarded message:",
    r"^-{3,}\s*Original Message\s*-{3,}",
    r"^_{5,}\s*$",  # Outlook draws a line before the forwarded header
]
HEADER_LINE = re.compile(r"^(From|Sent|Date|To|Cc|Subject|Reply-To)\s*:", re.I)


def strip_forwarding(text: str) -> str:
    """Drop everything up to and including the forwarded-message header block
    (your own note and signature sit above it)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if any(re.match(m, s, re.I) for m in FWD_MARKERS):
            start = i + 1
            break
        # Outlook sometimes has no marker line, just a From:/Sent: block
        if re.match(r"^From\s*:", s, re.I) and i + 1 < len(lines) and HEADER_LINE.match(lines[i + 1].strip()):
            start = i
            break
    if start is None:
        return text
    j = start
    while j < len(lines) and (HEADER_LINE.match(lines[j].strip()) or not lines[j].strip()):
        j += 1
    return "\n".join(lines[j:])


def find_url(text: str):
    urls = re.findall(r"https?://[^\s<>\"]+", text)
    return urls[0].rstrip(").,>") if urls else None


def fetch_article(url: str):
    """Download a web page and pull out the article text and title."""
    import trafilatura

    try:
        downloaded = trafilatura.fetch_url(url)
    except Exception as e:
        print(f"Download failed: {e}")
        return None, None
    if not downloaded:
        return None, None
    body = trafilatura.extract(downloaded, include_comments=False, include_tables=True,
                               favor_precision=True)
    meta = trafilatura.extract_metadata(downloaded)
    title = meta.title if meta and meta.title else None
    return title, body


# Footnote numbers glued to the end of a sentence or word: "milk.1 If", "protein.2–5",
# "DV).6", "reagents.21". Decimals like "0.35" and names like "B12" are left alone.
CITE_AFTER_PUNCT = re.compile(
    r"(?<=[A-Za-z\)%”\"’])([\.\,;:\)”\"’]+)(\d{1,3}(?:\s?[–\-,]\s?\d{1,3})*)(?=\s|$)")
UNITS = {
    "mg/mL": "milligrams per milliliter", "CFU/mL": "colony-forming units per milliliter",
    "mg/dL": "milligrams per deciliter", "mg": "milligrams", "kg": "kilograms",
    "km": "kilometers", "mph": "miles per hour", "bps": "basis points",
}
CITE_AFTER_WORD = re.compile(r"(?<=[a-z]{3})(\d{1,2}(?:[–\-,]\d{1,2})*)(?=[\s\.,;:]|$)")


def clean_paragraph(p: str) -> str:
    p = re.sub(r"https?://\S+", "", p)
    p = re.sub(r"\bmailto:\S*", "", p)
    # image descriptions and share buttons in [brackets]; short [insertions] in quotes are kept
    p = re.sub(r"\[\s*(?:photo|image|illustration|chart|graphic|video|logo|share|tweet|post to|email this|"
               r"axios on|follow|view in|a (?:line|bar)? ?chart)[^\]]*\]?", "", p, flags=re.I)
    p = re.sub(r"\[[^\]]{40,}\]", "", p)
    if re.fullmatch(r"(?:\s*\[[^\]]*\]?[\s.,|]*)+", p):
        return ""
    p = re.sub(r"(^|\s)\*+(?=\s)", r"\1", p)                  # inline "*" bullets
    p = re.sub(r"\*(\S(?:[^*]*?\S)?)\*", r"\1", p)            # *emphasis*
    p = re.sub(r"\s+[|•·]\s+", ". ", p)               # " | " and " · " separators
    # words run together where a link used to be: "HuangBuilt", "ofAnthropic"
    p = re.sub(r"\b([a-z]{2,}|[A-Z][a-z]{2,})([A-Z][a-z]{2,})\b", r"\1 \2", p)
    p = re.sub(r"([a-z]{2})\.([A-Z][a-z])", r"\1. \2", p)     # "so.Next" -> "so. Next"
    p = re.sub(r"^[*•·▪●]\s+", "", p)              # list bullets
    p = re.sub(r"\[\d+(?:[–\-,]\d+)*\]", "", p)            # [12] style references
    p = re.sub(r"\b((?:19|20)\d\d)\.\d{1,3}(?=\s|$)", r"\1.", p)   # "2014.18" -> "2014."
    p = CITE_AFTER_PUNCT.sub(lambda m: m.group(1), p)
    for unit, spoken in UNITS.items():
        p = re.sub(rf"(?<=\d)\s*{re.escape(unit)}\b", " " + spoken, p)
        p = re.sub(rf"\b{re.escape(unit)}\b", spoken, p)
    p = CITE_AFTER_WORD.sub("", p)
    p = p.replace("\t", ", ")                             # table cells
    p = re.sub(r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit", p)
    p = re.sub(r"(\d)\s*°\s*C\b", r"\1 degrees Celsius", p)
    p = re.sub(r"~\s*(?=\d)", "about ", p)
    p = re.sub(r"<\s*(?=\d)", "less than ", p)
    p = re.sub(r">\s*(?=\d)", "more than ", p)
    p = re.sub(r"#(?=\d)", "number ", p)
    p = re.sub(r"(\d)\s*[–-]\s*(\d)", r"\1 to \2", p)       # 4–5 -> 4 to 5
    p = p.replace("—", ", ").replace("–", ", ")
    p = re.sub(r"\s+([,.;:!?])(?=[\s\"”’)]|$)", r"\1", p)     # "word ." -> "word."
    p = re.sub(r"[ \t]{2,}", " ", p)
    return p.strip()


def to_paragraphs(text: str):
    """Split into paragraphs, rejoin hard-wrapped lines, and mark headings."""
    text = text.replace("\r\n", "\n").replace("\u00a0", " ")
    # invisible padding characters newsletters put after the preview line
    text = "".join(ch for ch in text
                   if ch == "\u00b0" or (unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co")
                                          and not 0x2190 <= ord(ch) <= 0x21ff
                                          and not 0xfe00 <= ord(ch) <= 0xfe0f and ch != "\u20e3"))
    text = re.sub("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f"
                  "\u2028-\u202e\u2060-\u2064\u3164\ufeff\uffa0]", "", text)
    # leftovers of links: a "<" or "<URL>" on its own line sat mid-sentence, so rejoin it
    text = re.sub(r"https?://[^\s>]+", "", text)
    text = re.sub(r"\[image:[^\]]*\]", "", text)
    text = re.sub(r"(?im)^[ \t]*(read|view) in (your )?browser[ \t]*$", "", text)
    text = re.sub(r"[ \t]*\n[ \t]*[<>][<> \t]*\n[ \t]*(?=\S)", " ", text)
    text = re.sub(r"<(?!\s*\d)|(?<!\d)>(?!\s*\d)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = "\n".join(l.strip() for l in text.split("\n"))
    # Each line is its own paragraph, except lines that look hard-wrapped
    # (long, and not ending a sentence), which are joined to the next line.
    blocks, cur = [], ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if cur:
                blocks.append(cur)
            cur = ""
            continue
        if cur and "\t" not in cur and "\t" not in line and 50 <= len(cur) and not re.search(r"[.!?:;\"”)][\d,–-]*$", cur):
            cur += " " + line
        else:
            if cur:
                blocks.append(cur)
            cur = line
    if cur:
        blocks.append(cur)

    out = []
    for b in blocks:
        if "\t" in b:  # a table row: drop footnote numbers from the last cell, read as one sentence
            cells = [c.strip() for c in b.split("\t") if c.strip()]
            if cells:
                cells[-1] = re.sub(r"(?<=[A-Za-z\)])\d{1,3}(?:[,–-]\d{1,3})*$", "", cells[-1])
            para = clean_paragraph(", ".join(cells))
            if para:
                out.append((para if re.search(r"[.!?]$", para) else para + ".", False))
            continue
        para = clean_paragraph(b)
        if not para or re.fullmatch(r"[\W\d_]+", para):
            continue
        heading = len(para) < 90 and not re.search(r"[.!?:;”\"]$", para)
        if heading:
            para += "."
        out.append((para, heading))
    return drop_junk(out)


# ---- newsletter junk: sponsor blocks, photo credits, share buttons, footers ----

AD_LINE = re.compile(r"^(presented by|sponsored by|brought to you by|powered by|in partnership with)\b", re.I)
AD_BLOCK = re.compile(r"^(a message from|advertisement|sponsored( content)?|partner content|together with|"
                      r"from our (partners?|sponsors?)|a word from)\b", re.I)
CALL_TO_ACTION = re.compile(r"^(get the|get your|buy|shop|learn more|sign up|register|subscribe|download|contact us|"
                            r"discover|join|claim|order|book now|reserve|apply|request|find out|click|see how)\b|"
                            r"(learn more|sign up|get started|buy now|shop now|order now)[.!]?$", re.I)
CREDIT = re.compile(r"^(photo( illustration)?|illustration|images?|chart|graphic|data|sources?|credit|video|map|"
                    r"stock)s?:\s", re.I)
NOISE = re.compile(r"(view (it |this )?(in (your )?|on )?(browser|online|web)|web version|"
                   r"\b[\d,]+ words, an? [\d.]+.minute read|^share (this|on)\b|^forward(ed)? (this|to a friend)|"
                   r"^go deeper\b|^read (more|the full|on)\b|^continue reading)", re.I)
FOOTER = re.compile(r"^(thanks for reading|thank you for reading|thanks to .{0,60} for (editing|copy)|"
                    r"we'?d (like|love) (your feedback|to hear from you)|tell your friends|"
                    r"if (you received|someone forwarded) this|you (are |were )?(received|receiving) this|"
                    r"you'?re receiving this|to stop receiving|unsubscribe|manage (your )?(email )?preferences|"
                    r"advertise with us|.{0,25}thanks our partners|follow .{0,30}\b(on|across)\b|connect with us|"
                    r"why stop here|please invite your friends|like this .{0,30}(style|format)|copyright|"
                    r"all rights reserved|need help\?|privacy policy|was this (email|newsletter) forwarded)", re.I)


def drop_junk(paras):
    out, skip = [], 0
    n = len(paras)
    for i, (p, h) in enumerate(paras):
        if skip:
            skip -= 1
            if (CALL_TO_ACTION.search(p) and len(p) < 120) or i + 1 < n and re.match(r"^\d+\.\s", paras[i + 1][0]):
                skip = 0           # the sponsor block ends with its "buy now" / before the next numbered story
            continue
        if i > n * 0.55 and FOOTER.search(p):
            break                  # everything after this is the email footer
        if AD_BLOCK.search(p) and len(p) < 80:
            skip = 6               # drop the sponsor block that follows (at most 6 paragraphs)
            continue
        if AD_LINE.search(p) and len(p) < 80:
            continue
        if (CREDIT.search(p) and len(p) < 250) or (NOISE.search(p) and len(p) < 200):
            continue
        out.append((p, h))
    return out


def tidy_title(subject: str) -> str:
    t = "".join(ch for ch in (subject or "") if unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co")
                and not 0xfe00 <= ord(ch) <= 0xfe0f and ch != "\u200d")
    t = re.sub(r"^\s*((fwd?|fw|re)\s*:\s*)+", "", t, flags=re.I).strip()
    return t


# --------------------------------------------------------------------------
# 2. Reading it aloud
# --------------------------------------------------------------------------

def narrate(paragraphs, wav_path):
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    k = Kokoro(os.path.join(MODEL_DIR, "kokoro-v1.0.onnx"),
               os.path.join(MODEL_DIR, "voices-v1.0.bin"))
    sr = 24000
    pause = lambda s: np.zeros(int(sr * s), dtype=np.float32)
    chunks = [pause(0.5)]
    for i, (p, heading) in enumerate(paragraphs, 1):
        audio, _ = k.create(p, voice=VOICE, speed=SPEED, lang="en-us")
        if heading:
            chunks.append(pause(0.6))
        chunks.append(audio.astype(np.float32))
        chunks.append(pause(0.9 if heading else 0.6))
        print(f"  narrated {i}/{len(paragraphs)}", flush=True)
    chunks.append(pause(1.0))
    sf.write(wav_path, np.concatenate(chunks), sr)


# --------------------------------------------------------------------------
# 3. Updating the feed
# --------------------------------------------------------------------------

def load_feed():
    ET.register_namespace("itunes", ITUNES)
    if os.path.exists(FEED_FILE):
        tree = ET.parse(FEED_FILE)
        return tree, tree.getroot().find("channel")
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "Evan's Reading List"
    ET.SubElement(channel, "link").text = BASE_URL
    ET.SubElement(channel, "description").text = "Articles sent by email, read aloud."
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{ITUNES}}}author").text = "Evan"
    ET.SubElement(channel, f"{{{ITUNES}}}explicit").text = "false"
    ET.SubElement(channel, f"{{{ITUNES}}}type").text = "episodic"
    return ET.ElementTree(root), channel


def add_to_feed(title, summary, filename, size, duration_s, when):
    tree, channel = load_feed()
    url = f"{BASE_URL}/audio/{filename}"
    if url in [i.findtext("guid") for i in channel.findall("item")]:
        print("Episode already in feed; skipping.")
        return
    pub = format_datetime(when)
    lb = channel.find("lastBuildDate")
    if lb is None:
        lb = ET.SubElement(channel, "lastBuildDate")
    lb.text = pub

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "pubDate").text = pub
    ET.SubElement(item, "guid", isPermaLink="false").text = url
    ET.SubElement(item, "description").text = summary
    enc = ET.SubElement(item, "enclosure", url=url, length=str(size), type="audio/mpeg")
    ET.SubElement(item, f"{{{ITUNES}}}title").text = title
    ET.SubElement(item, f"{{{ITUNES}}}duration").text = str(int(duration_s))
    ET.SubElement(item, f"{{{ITUNES}}}explicit").text = "false"

    items = channel.findall("item")
    if items:
        channel.insert(list(channel).index(items[0]), item)
    else:
        channel.append(item)

    # keep only the newest KEEP episodes so the site stays small
    for old in channel.findall("item")[KEEP:]:
        old_url = old.find("enclosure").get("url")
        old_file = os.path.join(AUDIO_DIR, old_url.rsplit("/", 1)[-1])
        if os.path.exists(old_file):
            os.remove(old_file)
        channel.remove(old)

    ET.indent(tree, space="  ")
    tree.write(FEED_FILE, encoding="unicode", xml_declaration=True)
    print(f"Feed updated: {title}")


# --------------------------------------------------------------------------

def main():
    payload = json.loads(os.environ["ARTICLE_PAYLOAD"])
    subject = tidy_title(payload.get("subject", ""))
    text = payload.get("text", "") or ""
    when = datetime.now(timezone.utc)
    if payload.get("date"):
        try:
            when = datetime.fromisoformat(payload["date"].replace("Z", "+00:00"))
        except ValueError:
            pass

    body = strip_forwarding(text).strip()

    # If the email is basically just a link, go and get the article.
    url = find_url(body)
    words_without_links = len(re.sub(r"https?://\S+", "", body).split())
    if url and words_without_links < 80:
        print(f"Fetching article from {url}")
        page_title, page_text = fetch_article(url)
        if page_text:
            body = page_text
            subject = subject or page_title or ""
            if page_title and len(subject) < 4:
                subject = page_title
        else:
            print("Could not read that page (it may be paywalled).")

    paragraphs = to_paragraphs(body)
    if sum(len(p) for p, _ in paragraphs) < 200:
        sys.exit("Not enough article text found in that email; nothing to read.")

    if not subject:
        subject = paragraphs[0][0].rstrip(".")[:80]
    # don't read the title twice if the article starts with it
    if paragraphs and paragraphs[0][0].rstrip(".").lower() == subject.lower():
        paragraphs = paragraphs[1:]

    total = 0
    trimmed = []
    for p in paragraphs:
        total += len(p[0])
        if total > MAX_CHARS:
            trimmed.append(("The rest of this article was cut off because it was very long.", False))
            break
        trimmed.append(p)
    title_line = subject if re.search(r"[.!?]$", subject) else subject + "."
    paragraphs = [(title_line, True)] + trimmed

    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:50] or "article"
    filename = f"{when.strftime('%Y-%m-%d-%H%M')}-{slug}.mp3"
    os.makedirs(AUDIO_DIR, exist_ok=True)
    mp3_path = os.path.join(AUDIO_DIR, filename)

    if os.environ.get("DRY_RUN"):
        for p, h in paragraphs:
            print(("## " if h else "") + p + "\n")
        return

    print(f"Narrating '{subject}' ({len(paragraphs)} paragraphs)")
    narrate(paragraphs, "narration.wav")
    os.system(
        f'ffmpeg -y -loglevel error -i narration.wav -af "loudnorm=I=-16:TP=-1.5" '
        f'-ac 1 -ar 24000 -c:a libmp3lame -b:a 64k '
        f'-metadata title={json.dumps(subject)} -metadata album="Evan\'s Reading List" '
        f'"{mp3_path}"')
    import soundfile as sf
    duration = sf.info("narration.wav").duration
    os.remove("narration.wav")

    summary = " ".join(p for p, h in paragraphs[1:4] if not h)[:400]
    add_to_feed(subject, summary, filename, os.path.getsize(mp3_path), duration, when)


if __name__ == "__main__":
    main()
