"""
Shared Telegram Bot API helpers used by both bot.py and exam_server.py.

Centralises file-download and message-sending logic that was previously
duplicated between the two modules.
"""

from typing import Optional, Dict

import httpx

from shared.config import BOT_TOKEN, CF_WORKER_URL


async def tg_file_bytes(file_id: str) -> Optional[bytes]:
    """Download a Telegram file by file_id. Tries CF Worker first, then direct API."""
    print(f"[tg-file] start, file_id={file_id[:20]}..., BOT_TOKEN_set={bool(BOT_TOKEN)}, CF_WORKER_URL={CF_WORKER_URL}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{CF_WORKER_URL}/tg-file",
                params={"file_id": file_id},
                headers={"X-Bot-Token": BOT_TOKEN},
            )
            print(f"[tg-file] CF Worker /tg-file status={r.status_code}, content_len={len(r.content) if r.content else 0}")
            if r.status_code == 200 and r.content:
                return r.content
    except Exception as e:
        print(f"[tg-file] CF Worker fetch EXCEPTION: {type(e).__name__}: {e}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            file_resp = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id},
            )
            print(f"[tg-file] getFile status={file_resp.status_code}")
            file_data = file_resp.json()
            print(f"[tg-file] getFile response ok={file_data.get('ok')}, full={file_data if not file_data.get('ok') else '(ok)'}")
            if file_data.get('ok'):
                file_path = file_data['result']['file_path']
                img_resp = await client.get(
                    f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                )
                print(f"[tg-file] file download status={img_resp.status_code}, content_len={len(img_resp.content) if img_resp.content else 0}")
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        print(f"[tg-file] Direct Telegram fetch EXCEPTION: {type(e).__name__}: {e}")
    print(f"[tg-file] FAILED — returning None for file_id={file_id[:20]}...")
    return None


async def tg_send_message(chat_id: int, text: str, reply_to: int = None,
                          parse_mode: str = None) -> Optional[Dict]:
    """Send a text message via the Telegram Bot API through the CF Worker proxy."""
    if not chat_id:
        return None
    payload: Dict = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    url = f"{CF_WORKER_URL}/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"tg_send_message error: {e}")
    return None
