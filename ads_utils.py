import os
import hashlib
import mimetypes
import subprocess
import requests


def _get_mime_type(file_path: str, fallback: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    return mime if mime else fallback


def _convert_to_h264(input_path: str) -> str:
    """Convert any video to H.264 MP4 — the only format Meta accepts.
    Returns path to the converted file (caller must delete it).
    """
    output_path = os.path.splitext(input_path)[0] + "_h264.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise Exception(
            f"ffmpeg conversion failed: {result.stderr.decode(errors='replace')}"
        )
    return output_path


def _get_snapchat_access_token() -> str:
    url = "https://accounts.snapchat.com/login/oauth2/access_token"
    refresh_token = (os.getenv("SNAPCHAT_ADS_REFRESH_TOKEN") or "").strip()
    client_id = (os.getenv("SNAPCHAT_ADS_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("SNAPCHAT_ADS_CLIENT_SECRET") or "").strip()
    if not (refresh_token and client_id and client_secret):
        raise Exception(
            "Snapchat غير مهيأ: "
            "SNAPCHAT_ADS_REFRESH_TOKEN و SNAPCHAT_ADS_CLIENT_ID و SNAPCHAT_ADS_CLIENT_SECRET مطلوبة"
        )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = requests.post(url, data=data)
    if not response.ok:
        raise Exception(f"Snapchat token error {response.status_code}: {response.text}")
    return response.json()["access_token"]


def upload_to_meta(file_path: str, file_type: str,
                   account_id: str = None, access_token: str = None) -> dict:
    """Upload image or video to Meta Ads.
    account_id / access_token override env vars for multi-account support.
    """
    token = access_token or os.getenv("META_ADS_ACCESS_TOKEN")
    ad_account_id = account_id or os.getenv("META_ADS_ACCOUNT_ID")
    if not (token or "").strip() or not (ad_account_id or "").strip():
        raise Exception("Meta غير مهيأ: META_ADS_ACCESS_TOKEN و META_ADS_ACCOUNT_ID مطلوبة")

    if file_type == "video":
        # Always convert to H.264 MP4 — Meta only accepts this codec
        converted_path = _convert_to_h264(file_path)
        try:
            url = f"https://graph-video.facebook.com/v22.0/{ad_account_id}/advideos"
            file_name = os.path.basename(converted_path)
            with open(converted_path, "rb") as f:
                response = requests.post(
                    url,
                    data={
                        "access_token": token,
                        "name": os.path.splitext(os.path.basename(file_path))[0],
                    },
                    files={"source": (file_name, f, "video/mp4")},
                )
        finally:
            if os.path.exists(converted_path):
                os.remove(converted_path)
    else:
        mime = _get_mime_type(file_path, "image/jpeg")
        url = f"https://graph.facebook.com/v22.0/{ad_account_id}/adimages"
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            response = requests.post(
                url,
                data={"access_token": token},
                files={file_name: (file_name, f, mime)},
            )

    if not response.ok:
        raise Exception(f"Meta API error {response.status_code}: {response.text}")
    return response.json()


def upload_to_snapchat(file_path: str, file_type: str) -> dict:
    access_token = _get_snapchat_access_token()
    ad_account_id = (os.getenv("SNAPCHAT_ADS_AD_ACCOUNT_ID") or "").strip()
    if not ad_account_id:
        raise Exception("Snapchat غير مهيأ: SNAPCHAT_ADS_AD_ACCOUNT_ID مطلوب")
    headers = {"Authorization": f"Bearer {access_token}"}

    create_url = f"https://adsapi.snapchat.com/v1/adaccounts/{ad_account_id}/media"
    media_data = {
        "media": [{
            "name": os.path.basename(file_path),
            "type": "VIDEO" if file_type == "video" else "IMAGE",
            "ad_account_id": ad_account_id,
        }]
    }
    create_res = requests.post(create_url, headers=headers, json=media_data)
    if not create_res.ok:
        raise Exception(f"Snapchat create media error {create_res.status_code}: {create_res.text}")

    media_id = create_res.json()["media"][0]["media"]["id"]

    mime = _get_mime_type(file_path, "video/mp4" if file_type == "video" else "image/jpeg")
    upload_url = f"https://adsapi.snapchat.com/v1/media/{media_id}/upload"
    with open(file_path, "rb") as f:
        upload_res = requests.post(
            upload_url,
            headers=headers,
            files={"file": (os.path.basename(file_path), f, mime)},
        )
    if not upload_res.ok:
        raise Exception(f"Snapchat upload error {upload_res.status_code}: {upload_res.text}")
    return upload_res.json()


def upload_to_tiktok(file_path: str, file_type: str = "video") -> dict:
    access_token = (os.getenv("TIKTOK_ADS_ACCESS_TOKEN") or "").strip()
    advertiser_id = (os.getenv("TIKTOK_ADS_ADVERTISER_ID") or "").strip()
    if not access_token or not advertiser_id:
        raise Exception("TikTok غير مهيأ: TIKTOK_ADS_ACCESS_TOKEN و TIKTOK_ADS_ADVERTISER_ID مطلوبة")
    headers = {"Access-Token": access_token}
    file_name = os.path.basename(file_path)

    if file_type == "image":
        url = "https://business-api.tiktok.com/open_api/v1.3/file/image/ad/upload/"
        mime = _get_mime_type(file_path, "image/jpeg")
        data = {
            "advertiser_id": advertiser_id,
            "image_name": file_name,
        }
        with open(file_path, "rb") as f:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                files={"image_file": (file_name, f, mime)},
            )
    else:
        with open(file_path, "rb") as f:
            video_signature = hashlib.md5(f.read()).hexdigest()

        url = "https://business-api.tiktok.com/open_api/v1.3/file/video/ad/upload/"
        mime = _get_mime_type(file_path, "video/mp4")
        data = {
            "advertiser_id": advertiser_id,
            "video_signature": video_signature,
            "video_name": file_name,
        }
        with open(file_path, "rb") as f:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                files={"video_file": (file_name, f, mime)},
            )

    if not response.ok:
        raise Exception(f"TikTok upload error {response.status_code}: {response.text}")
    return response.json()
