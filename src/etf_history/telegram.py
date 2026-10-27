"""Telegram Bot API: send the morning report, find the chat id. The token is part of every URL, so errors are
re-raised without it."""

import httpx

from etf_history import http

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LENGTH = 4096  # characters per message


class TelegramError(Exception):
    pass


def _call(token: str, method: str, **params) -> dict | list:
    try:
        with http.client(timeout=30) as client:
            resp = client.post(API.format(token=token, method=method), json=params)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramError(f"{method}: {type(exc).__name__}") from None
    if not body.get("ok"):
        raise TelegramError(f"{method}: {resp.status_code} {body.get('description', '')}")
    return body["result"]


def chunks(text: str, limit: int = MAX_LENGTH) -> list[str]:
    """Split at line breaks into messages of at most `limit` characters."""
    out, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single line longer than a message
            out, current = [*out, current] if current else out, ""
            out.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            out.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    return [*out, current] if current else out


def send(token: str, chat_id: str, text: str) -> None:
    for part in chunks(text):
        _call(token, "sendMessage", chat_id=chat_id, text=part, disable_web_page_preview=True)


def chats(token: str) -> dict[str, str]:
    """Chats that wrote to the bot recently (getUpdates keeps ~24 h): id → name."""
    found = {}
    for update in _call(token, "getUpdates"):
        chat = (update.get("message") or update.get("my_chat_member") or {}).get("chat")
        if chat:
            found[str(chat["id"])] = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name"), chat.get("username")])
            )
    return found
