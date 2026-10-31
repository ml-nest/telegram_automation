import csv
import gzip
import math
import re
import shutil
import tempfile
import unicodedata
import urllib.request
from bisect import bisect_left
from collections import defaultdict
from rapidfuzz import fuzz, process
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "chat_messages.csv"
OUTPUT_FILE = BASE_DIR / "chat_messages_with_imdb_ratings.csv"
DATASET_URLS = {
    "basics": "https://datasets.imdbws.com/title.basics.tsv.gz",
    "ratings": "https://datasets.imdbws.com/title.ratings.tsv.gz",
}
TITLE_TYPES = {"movie", "tvMovie", "tvSeries", "tvMiniSeries"}
SERIES_MARKER = re.compile(
    r"\s+(?:S\d{1,2}(?:E\d{1,2})?|SEASON\s+\d+)\b",
    re.IGNORECASE,
)
NOISE_MARKER = re.compile(
    r"\b(?:1080p|720p|2160p|4k|ds4k|10bit|web[\s.-]?dl|webdl|hdrip|"
    r"brrip|bluray|blu-ray|remastered|extended|uncut|unrated|imax|hdtc|"
    r"hdts|aac|ddp|dd5|mkv|h264|x264|hevc|hdr10)\b",
    re.IGNORECASE,
)
YEAR_IN_PARENS = re.compile(r"\s*\(((?:19|20)\d{2})\)")
TRAILING_YEAR = re.compile(r"\s+((?:19|20)\d{2})$")


def normalize_title(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if character.isalnum())


def title_tokens(value):
    return {
        normalize_title(token)
        for token in re.findall(r"[^\W_]+", value.casefold())
        if normalize_title(token)
    }


def parse_cleaned_name(value):
    title = value.strip()
    year = None

    noise_match = NOISE_MARKER.search(title)
    if noise_match:
        title = title[:noise_match.start()]

    year_match = YEAR_IN_PARENS.search(title)
    if year_match:
        year = int(year_match.group(1))
        title = title[:year_match.start()] + title[year_match.end():]
    else:
        year_match = TRAILING_YEAR.search(title)
        if year_match:
            year = int(year_match.group(1))
            title = title[:year_match.start()]

    series_match = SERIES_MARKER.search(title)
    is_series = series_match is not None
    if series_match:
        title = title[:series_match.start()]

    return title.strip(), year, is_series


def download_dataset(url, destination):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "telegram-automation-imdb-rating-export/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def load_title_index(path):
    titles_by_key = defaultdict(dict)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for row in reader:
            if row["titleType"] not in TITLE_TYPES:
                continue

            title = {
                "tconst": row["tconst"],
                "title_type": row["titleType"],
                "year": None if row["startYear"] == r"\N" else int(row["startYear"]),
            }
            for name in {row["primaryTitle"], row["originalTitle"]}:
                key = normalize_title(name)
                if key:
                    titles_by_key[key][title["tconst"]] = {
                        **title,
                        "title_name": name,
                    }

    return titles_by_key


def find_candidates(title, titles_by_key):
    words = title.split()
    while words:
        key = normalize_title(" ".join(words))
        candidates = titles_by_key.get(key)
        if candidates:
            return list(candidates.values())
        if len(key) < 6:
            break
        words.pop()
    return []


def load_ratings(path, wanted_ids):
    ratings = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for row in reader:
            if row["tconst"] in wanted_ids:
                ratings[row["tconst"]] = {
                    "rating": row["averageRating"],
                    "votes": int(row["numVotes"]),
                }
    return ratings


def choose_match(candidates, year, is_series, ratings, method):
    expected_types = (
        {"tvSeries", "tvMiniSeries"}
        if is_series
        else {"movie", "tvMovie"}
    )
    rated = [
        candidate
        for candidate in candidates
        if candidate["tconst"] in ratings
    ]
    typed_rated = [candidate for candidate in rated if candidate["title_type"] in expected_types]
    if typed_rated:
        rated = typed_rated
    elif method == "approximate":
        return None
    if not rated:
        return None

    def rank(candidate):
        title_type = candidate["title_type"]
        if is_series:
            type_match = title_type in {"tvSeries", "tvMiniSeries"}
        else:
            type_match = title_type in {"movie", "tvMovie"}
        year_match = year is not None and candidate["year"] == year
        return (
            type_match,
            year_match,
            int(candidate.get("match_score", 100) // 10),
            candidate.get("match_score", 100),
            ratings[candidate["tconst"]]["votes"],
        )

    best = max(rated, key=rank)
    return {
        "imdb_rating": ratings[best["tconst"]]["rating"],
        "imdb_match": best["title_name"],
        "match_method": method,
        "match_score": f"{best.get('match_score', 100):.1f}",
    }


def find_approximate_candidates(
    title,
    year,
    is_series,
    sorted_title_keys,
    titles_by_key,
):
    query = normalize_title(title)
    if len(query) < 4:
        return []
    query_tokens = title_tokens(title)
    expected_types = (
        {"tvSeries", "tvMiniSeries"}
        if is_series
        else {"movie", "tvMovie"}
    )

    minimum_length = max(
        4,
        math.ceil(len(query) * (0.8 if len(query) < 8 else 0.65)),
    )
    score_cutoff = 85 if len(query) < 8 else 78
    matches_by_key = {}
    for prefix_length in (4, 3, 2):
        prefix = query[:prefix_length]
        start = bisect_left(sorted_title_keys, prefix)
        end = bisect_left(sorted_title_keys, prefix + "\U0010ffff")
        choices = []
        year_matched_keys = []
        for key in sorted_title_keys[start:end]:
            compatible = [
                candidate
                for candidate in titles_by_key[key].values()
                if candidate["title_type"] in expected_types
            ]
            if not compatible:
                continue
            has_year_match = year is not None and any(
                candidate["year"] == year
                for candidate in compatible
            )
            if len(key) >= minimum_length or has_year_match:
                choices.append(key)
                if has_year_match:
                    year_matched_keys.append(key)
        if not choices:
            continue

        prefix_matches = process.extract(
            query,
            choices,
            scorer=fuzz.WRatio,
            limit=10,
            score_cutoff=score_cutoff,
        )
        matched_keys = {key for key, _, _ in prefix_matches}
        prefix_matches.extend(
            (key, score, None)
            for key in year_matched_keys
            if key not in matched_keys
            if (score := fuzz.WRatio(query, key)) >= 55
        )
        for key, score, _ in prefix_matches:
            matches_by_key[key] = max(score, matches_by_key.get(key, 0))

        has_year_match = year is not None and any(
            candidate["year"] == year
            for key in matches_by_key
            for candidate in titles_by_key[key].values()
        )
        if matches_by_key and (year is None or has_year_match):
            break

    matches = [(key, score, None) for key, score in matches_by_key.items()]
    candidates = []
    for key, score, _ in matches:
        for candidate in titles_by_key[key].values():
            match_score = fuzz.ratio(query, normalize_title(candidate["title_name"]))
            candidate_tokens = title_tokens(candidate["title_name"])
            if query_tokens and query_tokens.issubset(candidate_tokens) and (
                len(query_tokens) >= 2 or max(map(len, query_tokens)) >= 8
            ):
                match_score = max(
                    match_score,
                    fuzz.token_set_ratio(
                        " ".join(sorted(query_tokens)),
                        " ".join(sorted(candidate_tokens)),
                    ),
                )
            if match_score >= 85 and candidate["title_type"] in expected_types:
                candidates.append({
                    **candidate,
                    "match_score": match_score,
                })
    return candidates


def main():
    with SOURCE_FILE.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not {"message_id", "cleaned_name"}.issubset(reader.fieldnames or []):
            raise SystemExit(
                f"{SOURCE_FILE} must contain message_id and cleaned_name columns."
            )
        rows = list(reader)

    parsed_names = [parse_cleaned_name(row["cleaned_name"]) for row in rows]
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        basics_path = temporary_path / "title.basics.tsv.gz"
        ratings_path = temporary_path / "title.ratings.tsv.gz"

        for dataset_name, url in DATASET_URLS.items():
            print(f"Downloading IMDb {dataset_name} dataset...")
            destination = basics_path if dataset_name == "basics" else ratings_path
            download_dataset(url, destination)

        print("Indexing IMDb movie and series titles...")
        titles_by_key = load_title_index(basics_path)
        candidates_by_row = [
            find_candidates(title, titles_by_key)
            for title, _, _ in parsed_names
        ]
        wanted_ids = {
            candidate["tconst"]
            for candidates in candidates_by_row
            for candidate in candidates
        }

        print("Loading ratings for matched IMDb titles...")
        ratings = load_ratings(ratings_path, wanted_ids)

        matches_by_row = [
            choose_match(candidates, year, is_series, ratings, "exact")
            for candidates, (_, year, is_series) in zip(candidates_by_row, parsed_names)
        ]
        unresolved_indices = [
            index for index, match in enumerate(matches_by_row) if match is None
        ]

        if unresolved_indices:
            print(f"Trying approximate title matches for {len(unresolved_indices)} rows...")
            sorted_title_keys = sorted(titles_by_key)
            approximate_by_row = {}
            approximate_ids = set()
            for index in unresolved_indices:
                title, year, is_series = parsed_names[index]
                candidates = find_approximate_candidates(
                    title,
                    year,
                    is_series,
                    sorted_title_keys,
                    titles_by_key,
                )
                approximate_by_row[index] = candidates
                approximate_ids.update(
                    candidate["tconst"] for candidate in candidates
                )

            approximate_ratings = load_ratings(ratings_path, approximate_ids)
            ratings.update(approximate_ratings)
            for index, candidates in approximate_by_row.items():
                _, year, is_series = parsed_names[index]
                matches_by_row[index] = choose_match(
                    candidates,
                    year,
                    is_series,
                    ratings,
                    "approximate",
                )

    matched_count = 0
    approximate_count = 0
    unmatched_names = []
    for row, match in zip(rows, matches_by_row):
        if match:
            row.update(match)
            matched_count += 1
            approximate_count += match["match_method"] == "approximate"
        elif len(unmatched_names) < 20:
            row.update({
                "imdb_rating": "",
                "imdb_match": "",
                "match_method": "",
                "match_score": "",
            })
            unmatched_names.append(row["cleaned_name"])

    fieldnames = [
        "message_id",
        "cleaned_name",
        "imdb_rating",
        "imdb_match",
        "match_method",
        "match_score",
    ]
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Created {OUTPUT_FILE} with {len(rows)} rows.")
    print(
        f"Ratings matched: {matched_count} ({matched_count - approximate_count} exact, "
        f"{approximate_count} approximate); unmatched: {len(rows) - matched_count}."
    )
    if unmatched_names:
        print("Unmatched title examples:")
        for name in unmatched_names:
            print(f"- {name}")


if __name__ == "__main__":
    main()