import re


REMOVE_KEYWORDS = [
    "S-Print",
    "HC-ESub",
    "Hindi",
    "Telugu",
    "Korean",
    "Dual Audio",
    "Hindi or Korean",
    "x264",
    "x265",
    "HEVC",
    "WEBRip",
    "HDRip",
    "BluRay",
    "DVDRip",
    "WEB",
    "ESubs",
    "ESub",
    "Subtitles",
    "Subbed",
    "Movie",
    "Series",
    "Download",
]

def strip_prefix(value):
    value = normalize_text(value)
    value = re.sub(r"(?i)\s*(?:\[\s*@?\s*clipmatemovies\s*\]|\[\s*@?\s*clipmatezone\s*\]|\[\s*@?\s*clipmate\s*\]|@clipmatemovies|@clipmatezone|@clipmate)\s*[:\-]?\s*", " ", value)
    value = re.sub(r"\s*@\w+\b", " ", value, flags=re.I)
    value = value.replace("[", " ").replace("]", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_title(title):
    title = strip_prefix(title)
    title = remove_known_keywords(title)
    title = re.sub(r"^(?:movie|movies|series|show|tv|download|torrent)\s*[:\-]?\s*", "", title, flags=re.I)
    title = re.sub(r"\b(?:2160p|1080p|720p|480p|4k)\b", " ", title, flags=re.I)
    title = re.sub(r"\s*[-\.]\s*(?:1080p|720p|480p|2160p|4k)\s*$", "", title, flags=re.I)
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -")
    return title

def normalize_text(value):
    if value is None:
        return ""
    value = str(value)
    value = value.replace("**", "")
    value = value.replace("_", " ")
    value = value.replace("–", "-").replace("—", "-")
    value = value.replace("“", '"').replace("”", '"')
    for ch in ["'", '"', ":", ";", "!", "?", "’", "‘", "…"]:
        value = value.replace(ch, "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def remove_known_keywords(text):
    cleaned = normalize_text(text)

    for keyword in REMOVE_KEYWORDS:
        cleaned = re.sub(rf"(?i)\b{re.escape(keyword)}\b", " ", cleaned)

    cleaned = re.sub(r"\[(?:\s*Dual Audio\s*|\s*Hindi or Korean\s*|\s*Hindi\s*|\s*Korean\s*)\]", " ", cleaned, flags=re.I)
    cleaned = cleaned.replace("[", " ").replace("]", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -")



def normalize_for_dedupe(value):
    value = normalize_text(value).lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def extract_title_year_resolution(raw_text):
    text = strip_prefix(raw_text)
    text = remove_known_keywords(text)
    text = normalize_text(text)
    if not text:
        return {"title": "", "year": "", "resolution": ""}

    text = re.sub(r"^(?:movie|movies|series|show|tv|download|torrent)\s*[:\-]?\s*", "", text, flags=re.I)
    text = re.sub(r"\.(mkv|mp4|avi|mov|m4v|ts|m2ts|webm)$", "", text, flags=re.I)

    patterns = [
        r"(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*(?P<resolution>2160p|1080p|720p|480p|4k)\s*(?P<suffix>.*)",
        r"(?P<title>.+?)\s*-\s*(?P<year>\d{4})\s*-\s*(?P<resolution>2160p|1080p|720p|480p|4k)\s*(?P<suffix>.*)",
        r"(?P<title>.+?)\s*[\.\-]\s*(?P<year>\d{4})\s*[\.\-]\s*(?P<resolution>2160p|1080p|720p|480p|4k)\s*(?P<suffix>.*)",
        r"(?P<title>.+?)\s+(?P<resolution>2160p|1080p|720p|480p|4k)\s*(?P<suffix>.*)",
        r"(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*$",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            title = clean_title(match.group("title"))
            year = match.groupdict().get("year", "")
            resolution = (match.groupdict().get("resolution") or "").lower()
            suffix = (match.groupdict().get("suffix") or "").strip()
            if resolution == "4k":
                resolution = "2160p"

            if suffix and re.search(r"(?:s\d+|epi|episode)", suffix, flags=re.I):
                title = f"{title} {suffix}".strip()

            return {"title": title, "year": year, "resolution": resolution}

    # res_match = re.search(r"\b(2160p|1080p|720p|480p|4k)\b", text, flags=re.I)
    # if res_match:
    #     title = clean_title(text[:res_match.start()])
    #     resolution = res_match.group(1).lower()
    #     if resolution == "4k":
    #         resolution = "2160p"
    #     return {"title": title, "year": "", "resolution": resolution}

    year_match = re.search(r"\((?P<year>\d{4})\)", text)
    if year_match:
        title_text = text[:year_match.start()] + text[year_match.end():]
        return {
            "title": clean_title(title_text),
            "year": year_match.group("year"),
            "resolution": "",
        }

    return {"title": clean_title(text), "year": "", "resolution": ""}

 

def clean_movie_series_name(raw_message):
    raw_message = (raw_message or "").strip()
    if raw_message.startswith('`'):
        raw_message = raw_message[1:]
    if raw_message.endswith('`'):
        raw_message = raw_message[:-1]
    if re.match(r"^1\s*min\s+from\b", strip_prefix(raw_message), flags=re.I):
        return ""

    parsed = extract_title_year_resolution(raw_message)
    title = parsed["title"]
    if not title:
        return ""

    clean_name = title
    if parsed["year"]:
        clean_name = f"{title} ({parsed['year']})"

    if not ("S" in title.upper() or "EPI" in title.upper()) and re.search(
        r"\bS\d+\b|\bEpi\b|\bEpisode\b", raw_message, flags=re.I
    ):
        suffix = re.search(r"(S\d+\s*\[?Epi\s*\d+[\-\d]*\]?)", raw_message, flags=re.I)
        if suffix:
            clean_name = f"{clean_name} {suffix.group(1)}"

    return clean_name

