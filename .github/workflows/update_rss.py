#!/usr/bin/env python3
"""
update_rss.py
Updates feed.xml with the new podcast episode.
Run by GitHub Actions after each new MP3 is generated.
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate
import time

# ---- Read environment variables (set by GitHub Actions) ----
date_str = os.environ["NEWSLETTER_DATE"]           # e.g. "2024-03-18"
subject = os.environ["NEWSLETTER_SUBJECT"]          # e.g. "Money Stuff: ..."
github_username = os.environ.get("GITHUB_USERNAME", "YOUR_GITHUB_USERNAME")
github_repo = os.environ.get("GITHUB_REPO", "money-stuff-podcast")
file_size = os.environ.get("FILE_SIZE", "0")

# Base URL where your GitHub Pages site is hosted
BASE_URL = f"https://{github_username}.github.io/{github_repo}"
FEED_FILE = "feed.xml"
AUDIO_URL = f"{BASE_URL}/audio/{date_str}.mp3"

# Convert date string to RFC 2822 format for RSS
date_obj = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
pub_date = formatdate(time.mktime(date_obj.timetuple()))

# ---- Load or create the feed ----
ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"

if os.path.exists(FEED_FILE):
    tree = ET.parse(FEED_FILE)
    root = tree.getroot()
    channel = root.find("channel")
else:
    # Create a brand new feed
    root = ET.Element("rss", version="2.0", attrib={
        "xmlns:itunes": ITUNES,
        "xmlns:content": "http://purl.org/rss/1.0/modules/content/"
    })
    channel = ET.SubElement(root, "channel")

    ET.SubElement(channel, "title").text = "Money Stuff Podcast"
    ET.SubElement(channel, "link").text = BASE_URL
    ET.SubElement(channel, "description").text = (
        "Auto-generated audio version of Matt Levine's Money Stuff newsletter."
    )
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, "generator").text = "money-stuff-podcast-bot"

    itunes_author = ET.SubElement(channel, f"{{{ITUNES}}}author")
    itunes_author.text = "Matt Levine / Bloomberg"

    itunes_explicit = ET.SubElement(channel, f"{{{ITUNES}}}explicit")
    itunes_explicit.text = "false"

    itunes_type = ET.SubElement(channel, f"{{{ITUNES}}}type")
    itunes_type.text = "episodic"

    tree = ET.ElementTree(root)

# Update the lastBuildDate in the channel
last_build = channel.find("lastBuildDate")
if last_build is None:
    last_build = ET.SubElement(channel, "lastBuildDate")
last_build.text = pub_date

# ---- Check if this episode already exists ----
existing_guids = [item.findtext("guid") for item in channel.findall("item")]
if AUDIO_URL in existing_guids:
    print(f"Episode {date_str} already exists in feed. Skipping.")
else:
    # ---- Create the new item ----
    # Insert at the top (after channel metadata) so newest is first
    # Find position after last non-item element
    items = channel.findall("item")
    
    item = ET.Element("item")
    ET.SubElement(item, "title").text = subject
    ET.SubElement(item, "pubDate").text = pub_date
    ET.SubElement(item, "guid").text = AUDIO_URL
    ET.SubElement(item, "link").text = AUDIO_URL
    ET.SubElement(item, "description").text = (
        f"Auto-generated audio of Money Stuff newsletter: {subject}"
    )

    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", AUDIO_URL)
    enclosure.set("length", str(file_size))
    enclosure.set("type", "audio/mpeg")

    itunes_ep_title = ET.SubElement(item, f"{{{ITUNES}}}title")
    itunes_ep_title.text = subject

    itunes_ep_explicit = ET.SubElement(item, f"{{{ITUNES}}}explicit")
    itunes_ep_explicit.text = "false"

    # Insert new item before any existing items (newest first)
    if items:
        first_item_index = list(channel).index(items[0])
        channel.insert(first_item_index, item)
    else:
        channel.append(item)

    print(f"Added episode: {subject} ({date_str})")

# ---- Write the updated feed ----
ET.indent(tree, space="  ")
tree.write(FEED_FILE, encoding="unicode", xml_declaration=True)
print(f"Feed written to {FEED_FILE}")

