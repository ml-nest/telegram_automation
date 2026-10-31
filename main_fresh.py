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
    fieldnames = ["message_id", "cleaned_name"]
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
        required = {"message_id", "cleaned_name"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"{csv_file} must contain message_id and cleaned_name columns"
            )

        rows = [
            {
                "message_id": row["message_id"],
                "cleaned_name": row["cleaned_name"],
            }
            for row in reader
        ]

    with TelegramClient(str(BASE_DIR / "session"), API_ID, API_HASH) as client:
        source = client.get_entity(source_chat_id)
        destination = client.get_entity(destination_chat_id)

        # iter_messages yields newest first; keep the newest source post per name.
        for frmts in ['1080p', '720p']:
            latest_source_by_name = {}
            for source_message in client.iter_messages(source, limit=limit):
                if source_message.video and source_message.text and frmts in source_message.text:
                    if source_message.id in forwarded_ids:
                        continue

                    cleaned_name = clean_movie_series_name(source_message.message or "")
                    if not cleaned_name:
                        forwarded_ids.add(source_message.id)
                        continue

                    key = normalize_for_dedupe(cleaned_name)
                    if key not in latest_source_by_name:
                        latest_source_by_name[key] = {
                            "message": source_message,
                            "cleaned_name": cleaned_name,
                            "source_ids": [],
                        }
                    latest_source_by_name[key]["source_ids"].append(source_message.id)

            work = []
            skipped_existing_names = 0
            for key, source_data in latest_source_by_name.items():
                source_message = source_data["message"]
                cleaned_name = source_data["cleaned_name"]
                matching_rows = [
                    row for row in rows
                    if normalize_for_dedupe(row["cleaned_name"]) == key
                ]

                # A matching cleaned name is already represented in the destination.
                # Mark the source posts as handled without replacing that entry.
                if matching_rows:
                    skipped_existing_names += 1
                    forwarded_ids.update(source_data["source_ids"])
                    save_forwarded_ids()
                    continue

                work.append((
                    source_message,
                    cleaned_name,
                    source_data["source_ids"],
                ))

            print(
                f"Plan: {len(work)} source posts to send; "
                f"{skipped_existing_names} already represented by name (skipped)."
            )

            # if input("Type RUN to continue: ") != "RUN":
            #     print("Cancelled; no messages were changed.")
            #     return

            for source_message, cleaned_name, source_ids in work:
                sent = client.send_file(
                    destination,
                    source_message.video,
                    caption=cleaned_name,
                )

                if isinstance(sent, list):
                    sent = sent[0]

                rows.append({
                    "message_id": str(sent.id),
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
    api_id = os.environ["API_ID"]
    API_HASH = os.environ["API_HASH"]
    if not api_id or not API_HASH:
        raise SystemExit("Set api_id and api_hash in .env or your environment.")
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
        except:
            pass

if __name__ == "__main__":
    main()