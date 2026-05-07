import os
import io
import re
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


def _get_service():
    creds = Credentials(
        None,
        refresh_token=os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_DRIVE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_DRIVE_CLIENT_SECRET"),
    )
    if not creds.valid:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def get_file_id_from_link(link: str) -> str | None:
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]{25,})",
        r"[?&]id=([a-zA-Z0-9_-]{25,})",
        r"/d/([a-zA-Z0-9_-]{25,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return None


def get_folder_id_from_link(link: str) -> str | None:
    patterns = [
        r"/folders/([a-zA-Z0-9_-]{25,})",
        r"[?&]id=([a-zA-Z0-9_-]{25,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return None


def list_subfolders(folder_id: str) -> list:
    service = _get_service()
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id, name)", orderBy="name", pageSize=100,
    ).execute()
    return results.get("files", [])


def list_files_in_folder(folder_id: str) -> list:
    service = _get_service()
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType != 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    all_files = []
    page_token = None
    while True:
        kwargs = dict(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            orderBy="name",
            pageSize=1000,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.files().list(**kwargs).execute()
        all_files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return all_files


def download_file_from_drive(file_id: str, output_path: str) -> tuple:
    """Download file and return (local_path, mime_type) tuple.
    mime_type comes directly from Drive metadata — 100% accurate.
    """
    service = _get_service()
    file_meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    file_name = file_meta.get("name", file_id)
    mime_type = file_meta.get("mimeType", "")
    ext = os.path.splitext(file_name)[1]
    if ext and not os.path.splitext(output_path)[1]:
        output_path = output_path + ext
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(output_path, "wb")
    try:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    finally:
        fh.close()
    return output_path, mime_type


def download_file_by_name(file_id: str, file_name: str, output_dir: str) -> str:
    """Download keeping the original filename. Returns local path."""
    service = _get_service()
    output_path = os.path.join(output_dir, file_name)
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(output_path, "wb")
    try:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    finally:
        fh.close()
    return output_path
