# ==============================
# Install + Import Dependencies
# ==============================
# Run this once in your environment or put in requirements.txt
# PyMuPDF
# google-api-python-client
# google-auth
# google-auth-httplib2
# google-auth-oauthlib

import os
import io
import datetime
import tempfile
import fitz  # PyMuPDF
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ==============================
# Authenticate to Google Drive
# ==============================
SCOPES = ['https://www.googleapis.com/auth/drive']

creds = Credentials(
    token=None,
    refresh_token=os.environ['GOOGLE_REFRESH_TOKEN'],
    client_id=os.environ['GOOGLE_CLIENT_ID'],
    client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
    token_uri='https://oauth2.googleapis.com/token',
    scopes=SCOPES
)

drive_service = build('drive', 'v3', credentials=creds)

# ==============================
# Folder IDs
# ==============================
# These are the new folders for your Rice Crispy Treat + Fudge labels
UPDATING_LABELS_FOLDER_ID = "1DmxmpYMpIyeXOsRUH9mfBp9oEMT43clC"
ARCHIVE_FOLDER_ID = "1Ob6gnf2GazvTxSjqMquxUl1zepmmmzOF"

# ==============================
# Google Drive Helpers
# ==============================
def list_files_in_folder(folder_id):
    """Return all files in a folder (handles pagination)."""
    files = []
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return files

def download_file_to_path(file_id, local_path):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, "wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()

def upload_file_replace(file_id, local_path, mimetype="application/pdf"):
    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    updated_file = drive_service.files().update(
        fileId=file_id,
        media_body=media
    ).execute()
    return updated_file

def find_file_in_folder_by_name(folder_id, name):
    safe_name = name.replace('"', '\\"')
    query = f"'{folder_id}' in parents and name = \"{safe_name}\" and trashed=false"
    res = drive_service.files().list(q=query, fields='files(id, name)', spaces='drive').execute()
    files = res.get('files', [])
    return files[0] if files else None

def copy_file_to_folder(file_id, new_folder_id, new_name=None):
    file = drive_service.files().get(fileId=file_id, fields='name').execute()
    name = new_name if new_name else file['name']

    existing = find_file_in_folder_by_name(new_folder_id, name)
    if existing:
        drive_service.files().delete(fileId=existing['id']).execute()

    copied_file = {'name': name, 'parents': [new_folder_id]}
    return drive_service.files().copy(fileId=file_id, body=copied_file).execute()

# ==============================
# Date Utilities
# ==============================
def compute_best_by_date():
    """Compute best-by date: 75 days ahead, rounded to nearest 1st or 15th.
    If equal distance, choose whichever is sooner."""
    target = datetime.date.today() + datetime.timedelta(days=75)
    first = target.replace(day=1)
    fifteenth = target.replace(day=15)

    dist_first = abs((target - first).days)
    dist_fifteenth = abs((target - fifteenth).days)

    if dist_first < dist_fifteenth:
        rounded = first
    elif dist_fifteenth < dist_first:
        rounded = fifteenth
    else:
        # If tie, choose whichever comes first (sooner date)
        rounded = first if first < fifteenth else fifteenth

    return rounded.strftime("%m/%d/%Y")

# ==============================
# PDF Text Replacement
# ==============================
def replace_best_by_text(doc, new_date):
    replaced = False
    phrases_to_match = ["Best if used by:", "Best if Used By:"]  # support both capitalizations

    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    text = s.get("text", "").strip()
                    if any(text.startswith(p) for p in phrases_to_match):
                        old_text = text
                        new_text = f"Best if Used By: {new_date}"
                        bbox = fitz.Rect(s["bbox"])
                        rotation = 90 if bbox.height > bbox.width else 0

                        # White out old text
                        page.draw_rect(bbox, color=(1,1,1), fill=(1,1,1))

                        # Choose text insertion point based on rotation
                        if rotation == 0:
                            x, y = bbox.x0, bbox.y1
                        else:
                            x, y = bbox.x1, bbox.y1

                        page.insert_text(
                            (x, y),
                            new_text,
                            fontname="helv",
                            fontsize=s["size"],
                            color=(0,0,0),
                            rotate=rotation
                        )
                        print(f"Replaced on page {page_num}: '{old_text}' → '{new_text}' (rotation={rotation})")
                        replaced = True
    return replaced

# ==============================
# Main Processing
# ==============================
def process_labels():
    files = list_files_in_folder(UPDATING_LABELS_FOLDER_ID)
    pdf_files = [f for f in files if f.get('mimeType') == 'application/pdf' or f['name'].strip().lower().endswith('.pdf')]

    target_date = compute_best_by_date()
    print(f"Target best-by date: {target_date}\n")

    summary = []

    for f in pdf_files:
        file_id = f["id"]
        name = f["name"]
        print(f"Processing: {name} (id: {file_id})")

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = os.path.join(tmpdir, name)
            download_file_to_path(file_id, local_path)
            print(" - downloaded original")

            copy_file_to_folder(file_id, ARCHIVE_FOLDER_ID, name)
            print(f" - archived original as {name}")

            doc = fitz.open(local_path)
            replaced = replace_best_by_text(doc, target_date)

            if replaced:
                new_path = local_path + "_updated.pdf"
                doc.save(new_path, deflate=True)
                doc.close()
                os.replace(new_path, local_path)
                upload_file_replace(file_id, local_path)
                print(f" - ✅ updated best-by date to {target_date}\n")
                summary.append((name, "updated", target_date))
            else:
                doc.close()
                print(" - ⚠️ no matches found to replace\n")
                summary.append((name, "no-replace", None))

    print("\n=== Run summary ===")
    for item in summary:
        print(item)

# ==============================
# Run once
# ==============================
if __name__ == "__main__":
    process_labels()
