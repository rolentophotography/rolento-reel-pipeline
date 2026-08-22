#!/usr/bin/env python3
"""
drive_pipeline.py — watch a Google Drive folder of curated photos, turn each
new one into a 1080x1920 animated reel with photo_to_reel.py, and upload the
result to a second Drive folder. Designed to run on a schedule from GitHub
Actions (or any headless machine), the same way the VideoAutoGen pipeline
already pulls from / pushes to Drive.

Folder layout expected (matches Instagram_automation in Drive):
    01_chosen_by_me/   <- you drop curated photos here
    02_Reels_made/     <- this script uploads finished .mp4 reels here
                          (Make.com then watches this folder and posts them)
    03_Reels_Published/ <- Make.com moves posted files here when done

Auth: OAuth as YOUR OWN Google account (not a service account). Service
accounts have no personal storage quota, so they can read shared folders
but cannot create/own new files in a regular Google Drive -- uploading the
finished reels needs to happen as you, the actual Drive owner.

Credentials come from a one-time OAuth authorization (done once via Google's
OAuth Playground -- see the README), which produces a client ID, client
secret, and a refresh token. Store all three as GitHub secrets; this script
uses the refresh token to silently mint fresh access tokens on every run,
no browser interaction needed after the initial setup.

Env vars required:
    GOOGLE_OAUTH_CLIENT_ID       OAuth client ID
    GOOGLE_OAUTH_CLIENT_SECRET   OAuth client secret
    GOOGLE_OAUTH_REFRESH_TOKEN   OAuth refresh token (from OAuth Playground)
    DRIVE_FOLDER_SOURCE_ID       folder ID of 01_chosen_by_me
    DRIVE_FOLDER_OUTPUT_ID       folder ID of 02_Reels_made

Optional:
    REEL_DURATION   seconds per reel (default 12, matching the brand template)
    REEL_FPS        frames per second (default 30)
"""
import io
import os
import sys
import tempfile

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photo_to_reel import convert  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/drive"]
IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/tiff", "image/webp",
}
PROCESSED_MARKER_PREFIX = "_processed__"  # a tiny .done marker per source file


def get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def list_source_photos(service, folder_id):
    q = f"'{folder_id}' in parents and trashed = false"
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=q, spaces="drive",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return [f for f in results if f["mimeType"] in IMAGE_MIMES]


def list_output_names(service, folder_id):
    q = f"'{folder_id}' in parents and trashed = false"
    names = set()
    page_token = None
    while True:
        resp = service.files().list(
            q=q, spaces="drive",
            fields="nextPageToken, files(name)",
            pageToken=page_token,
        ).execute()
        names.update(f["name"] for f in resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return names


def download_file(service, file_id, dest_path):
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_file(service, local_path, name, folder_id):
    media = MediaFileUpload(local_path, mimetype="video/mp4", resumable=True)
    body = {"name": name, "parents": [folder_id]}
    service.files().create(body=body, media_body=media, fields="id").execute()


def main():
    source_id = os.environ["DRIVE_FOLDER_SOURCE_ID"]
    output_id = os.environ["DRIVE_FOLDER_OUTPUT_ID"]
    duration = float(os.environ.get("REEL_DURATION", "12"))
    fps = int(os.environ.get("REEL_FPS", "30"))

    service = get_service()
    photos = list_source_photos(service, source_id)
    existing_outputs = list_output_names(service, output_id)

    if not photos:
        print("No photos found in source folder.")
        return

    made = 0
    for photo in photos:
        base = os.path.splitext(photo["name"])[0]
        out_name = f"{base}_reel.mp4"
        if out_name in existing_outputs:
            continue  # already processed

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, photo["name"])
            out_path = os.path.join(td, out_name)
            print(f"Downloading {photo['name']} ...")
            download_file(service, photo["id"], src_path)
            try:
                orient = convert(src_path, out_path, duration=duration, fps=fps)
            except Exception as e:
                print(f"  FAILED to render {photo['name']}: {e}", file=sys.stderr)
                continue
            print(f"  rendered as {orient}, uploading {out_name} ...")
            upload_file(service, out_path, out_name, output_id)
            made += 1

    print(f"Done. {made} new reel(s) created.")


if __name__ == "__main__":
    main()
