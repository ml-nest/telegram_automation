import os
import csv
import json
from pathlib import Path
from telethon.sync import TelegramClient
from dotenv import load_dotenv
from cleaner import clean_movie_series_name, normalize_for_dedupe
from telethon import functions


BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()

destination_chat_id = -1001490981868 # Movies

def process_channel_to_chat(
    source_chat_id,
    destination_chat_id,
    csv_file,
    limit):
    fieldnames = ["message_id", "file_name", "cleaned_name"]
    forwarded_ids_file = BASE_DIR / "logs" / f"forwarded_source_{source_chat_id}.json"
    if os.path.exists(forwarded_ids_file):
        with open(forwarded_ids_file, "r", encoding="utf-8") as file:
            forwarded_ids = {int(message_id) for message_id in json.load(file)}
    else:
        forwarded_ids = set()

    def save_forwarded_ids():
        with open(forwarded_ids_file, "w", encoding="utf-8") as file:
            json.dump(sorted(forwarded_ids), file)

    # Keep existing CSV rows while normalizing them to the current schema.
    with open(csv_file, "r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        required = {"message_id", "file_name", "cleaned_name"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"{csv_file} must contain message_id, file_name, and cleaned_name columns"
            )

        rows = [
            {
                "message_id": row["message_id"],
                "file_name": row["file_name"],
                "cleaned_name": row["cleaned_name"],
            }
            for row in reader
        ]

    with TelegramClient(str(BASE_DIR / "session"), API_ID, API_HASH) as client:
        source = client.get_entity(source_chat_id)
        destination = client.get_entity(destination_chat_id)

        # iter_messages yields newest first, so the first entry for a title is its newest.
        candidates_by_resolution = {"1080p": {}, "720p": {}}
        for source_message in client.iter_messages(source, limit=limit):
            message_text = source_message.text or ""
            if not source_message.video or not message_text:
                continue

            file_name = source_message.file.name if source_message.file else ""
            if "#sample" in message_text.casefold():
                forwarded_ids.add(source_message.id)
                continue

            resolution = next(
                (value for value in candidates_by_resolution if value in message_text.lower()),
                None,
            )
            if resolution is None or source_message.id in forwarded_ids:
                continue

            cleaned_name = clean_movie_series_name(message_text)
            if not cleaned_name:
                forwarded_ids.add(source_message.id)
                continue

            key = normalize_for_dedupe(cleaned_name)
            candidates = candidates_by_resolution[resolution]
            if key not in candidates:
                candidates[key] = {
                    "message": source_message,
                    "cleaned_name": cleaned_name,
                    "source_ids": [],
                }
            candidates[key]["source_ids"].append(source_message.id)

        seen_file_names = {
            row["file_name"] for row in rows if row["file_name"]
        }
        higher_resolution_name_keys = set()
        work = []
        skipped_filename_duplicates = 0
        skipped_lower_resolutions = 0

        for resolution, candidates in candidates_by_resolution.items():
            for key, source_data in candidates.items():
                source_message = source_data["message"]
                cleaned_name = source_data["cleaned_name"]
                file_name = source_message.file.name if source_message.file else ""

                if resolution == "1080p":
                    higher_resolution_name_keys.add(key)
                elif key in higher_resolution_name_keys:
                    skipped_lower_resolutions += 1
                    forwarded_ids.update(source_data["source_ids"])
                    continue

                # A matching filename is already represented in the destination.
                if file_name and file_name in seen_file_names:
                    skipped_filename_duplicates += 1
                    forwarded_ids.update(source_data["source_ids"])
                    continue

                if file_name:
                    seen_file_names.add(file_name)
                work.append((
                    source_message,
                    cleaned_name,
                    file_name,
                    source_data["source_ids"],
                ))

        print(
            f"Plan: {len(work)} source posts to send; "
            f"{skipped_filename_duplicates} filename duplicates skipped; "
            f"{skipped_lower_resolutions} lower-resolution duplicates skipped."
        )

        for source_message, cleaned_name, file_name, source_ids in work:
            sent = client.send_file(
                destination,
                source_message.video,
                caption=cleaned_name,
            )

            if isinstance(sent, list):
                sent = sent[0]

            rows.append({
                "message_id": str(sent.id),
                "file_name": file_name,
                "cleaned_name": cleaned_name,
            })
            with open(csv_file, "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            forwarded_ids.update(source_ids)
            save_forwarded_ids()
            print(f"forwarded: source {source_message.id} -> destination {sent.id}")

        save_forwarded_ids()
        print(f"Finished processing {len(work)} source posts.")
        

def main():
    global API_ID, API_HASH

    load_dotenv(BASE_DIR / ".env")
    api_id = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    if not api_id or not API_HASH:
        raise SystemExit("Set API_ID and API_HASH in .env or your environment.")
    try:
        API_ID = int(api_id)
    except ValueError:
        raise SystemExit("api_id must be an integer.") from None

    csv_file = BASE_DIR / "chat_messages.csv"
    if not csv_file.is_file():
        raise SystemExit(f"CSV file not found: {csv_file}")
    

    with TelegramClient(str(BASE_DIR / "session"), API_ID, API_HASH) as client:
        result = client(functions.messages.GetDialogFiltersRequest())
    
        clipmate = next(
            (
                folder for folder in result.filters
                if getattr(getattr(folder, "title", None), "text", None) == "Clipmate"
            ),
            None,
        )
        if clipmate is None:
            raise SystemExit("Telegram folder 'Clipmate' was not found.")
    
        source_chats = {}
        for peer in clipmate.include_peers:
            source_chats[int('-100'+str(peer.channel_id))] = 'ch'


    for source_chat_id, source_chat_name in source_chats.items():
        print(f"Processing {source_chat_name} ({source_chat_id})")
        try:
            process_channel_to_chat(
                source_chat_id,
                destination_chat_id,
                csv_file=csv_file,
                limit=30000,
            )
        except Exception as error:
            print(f"Failed processing source {source_chat_id}: {error}")

if __name__ == "__main__":
    main()