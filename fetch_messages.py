#!/usr/bin/env python3
from __future__ import annotations
"""Retrieve Instagram conversation messages and their contents."""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, Optional

import requests

CONVERSATION_MESSAGES_URL_TEMPLATE = "https://graph.instagram.com/v22.0/{conversation_id}/messages"
MESSAGE_FIELDS = "id,from,to,message,created_time"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_created_time(value: Optional[str]) -> Optional[datetime]:
    """Convert an ISO8601 timestamp string into a datetime object."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def extract_message_text(data: dict) -> Optional[str]:
    """Return a textual representation of a message if available."""

    def _from_candidate(candidate: Optional[str]) -> Optional[str]:
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
        return None

    direct = _from_candidate(data.get("message"))
    if direct:
        return direct

    text_field = data.get("text")
    if isinstance(text_field, str):
        text = _from_candidate(text_field)
        if text:
            return text
    elif isinstance(text_field, dict):
        for key in ("text", "body", "message"):
            text = _from_candidate(text_field.get(key))
            if text:
                return text

    attachments = data.get("attachments")
    if isinstance(attachments, dict):
        for item in attachments.get("data", []):
            if not isinstance(item, dict):
                continue
            text = _from_candidate(item.get("text"))
            if text:
                return text
            payload = item.get("payload")
            if isinstance(payload, dict):
                for key in ("text", "body", "message"):
                    text = _from_candidate(payload.get(key))
                    if text:
                        return text

    return None


def fetch_conversation_page(
    conversation_id: str,
    token: str,
    *,
    page_url: Optional[str] = None,
    params: Optional[dict[str, str]] = None,
) -> tuple[list[dict], dict] | tuple[None, None]:
    """Return a single page of conversation metadata and paging info."""
    url = page_url or CONVERSATION_MESSAGES_URL_TEMPLATE.format(conversation_id=conversation_id)
    headers = {"Authorization": f"Bearer {token}"}
    if page_url is None:
        effective_params = {"fields": MESSAGE_FIELDS}
        if params:
            effective_params.update(params)
    else:
        effective_params = None

    try:
        response = requests.get(url, headers=headers, params=effective_params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - defensive
        print(f"error: failed to fetch conversation {conversation_id}: {exc}", file=sys.stderr)
        return None, None

    try:
        payload = response.json()
    except ValueError:  # pragma: no cover - invalid JSON
        print(f"error: conversation {conversation_id} did not return JSON", file=sys.stderr)
        return None, None

    container = payload.get("messages", payload)
    data = container.get("data", [])
    if not isinstance(data, list):
        data = []
    paging = container.get("paging", {}) or {}
    return data, paging


def iter_conversation_pages(
    conversation_id: str, token: str, page_limit: int = 0
) -> Iterator[tuple[list[dict], dict]]:
    """Yield pages of conversation metadata and their paging details."""
    url = None
    params: dict[str, str] | None = {}
    page_count = 0

    while True:
        if page_limit and page_count >= page_limit:
            return
        data, paging = fetch_conversation_page(
            conversation_id,
            token,
            page_url=url,
            params=params,
        )
        if data is None:
            return

        yield data, paging

        page_count += 1
        url = paging.get("next")
        params = None
        if not url:
            return


def iter_enriched_messages(
    conversation_id: str, token: str, order: str, page_limit: int
) -> Iterator[dict]:
    """Yield detailed message data for a conversation in the requested order."""
    key = lambda msg: (
        parse_created_time(msg.get("created_time")) or EPOCH,
        msg.get("id", ""),
    )

    if order == "asc":
        collected: list[dict] = []
        for page_messages, _paging in iter_conversation_pages(
            conversation_id, token, page_limit=page_limit
        ):
            for message in page_messages:
                normalized = normalize_message(message)
                if normalized is not None:
                    collected.append(normalized)
        if not collected:
            return
        collected.sort(key=key)
        for message in collected:
            yield message
        return

    for page_messages, _paging in iter_conversation_pages(
        conversation_id, token, page_limit=page_limit
    ):
        detailed_page: list[dict] = []
        for message in page_messages:
            normalized = normalize_message(message)
            if normalized is not None:
                detailed_page.append(normalized)
        if not detailed_page:
            continue
        detailed_page.sort(key=key, reverse=True)
        for message in detailed_page:
            yield message


def normalize_message(message: object) -> Optional[dict]:
    if not isinstance(message, dict):
        return None
    message_id = message.get("id")
    if not message_id:
        print("  skipping message without an id", file=sys.stderr)
        return None
    return dict(message)


@dataclass
class ConversationPage:
    identifier: Optional[str]
    messages: list[dict]
    next_url: Optional[str]
    previous_url: Optional[str]


class ConversationPager:
    """Lazy page fetcher that caches enriched conversation pages."""

    def __init__(
        self,
        conversation_id: str,
        token: str,
        *,
        order: str,
    page_limit: int,
) -> None:
        self._conversation_id = conversation_id
        self._token = token
        self._order = order
        self._page_limit = max(page_limit, 0)
        self._cache: dict[Optional[str], ConversationPage] = {}
        self._fetched = 0

    def fetch_page(self, page_url: Optional[str] = None) -> Optional[ConversationPage]:
        key = page_url
        if key in self._cache:
            return self._cache[key]
        if self._page_limit and self._fetched >= self._page_limit and key not in self._cache:
            return None

        messages, paging = fetch_conversation_page(
            self._conversation_id,
            self._token,
            page_url=page_url,
            params=None,
        )
        if messages is None:
            return None

        detailed: list[dict] = []
        for message in messages:
            normalized = normalize_message(message)
            if normalized is not None:
                detailed.append(normalized)

        key_func = lambda msg: (
            parse_created_time(msg.get("created_time")) or EPOCH,
            msg.get("id", ""),
        )
        reverse = self._order == "desc"
        detailed.sort(key=key_func, reverse=reverse)

        page = ConversationPage(
            identifier=page_url,
            messages=detailed,
            next_url=paging.get("next") if isinstance(paging, dict) else None,
            previous_url=paging.get("previous") if isinstance(paging, dict) else None,
        )
        self._cache[key] = page
        self._fetched += 1
        return page


def iter_conversation_ids(args: argparse.Namespace) -> Iterable[str]:
    if args.ids:
        yield from args.ids
    if not args.ids_file:
        return

    try:
        with open(args.ids_file, "r", encoding="utf-8") as handle:
            for line in handle:
                conversation_id = line.strip()
                if conversation_id:
                    yield conversation_id
    except OSError as exc:
        print(f"error: could not read ids file: {exc}", file=sys.stderr)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ids",
        metavar="CONVERSATION_ID",
        nargs="*",
        help="conversation IDs to fetch",
    )
    parser.add_argument(
        "--token",
        help="Instagram Graph API access token",
    )
    parser.add_argument(
        "--ids-file",
        help="Optional path to a file containing conversation IDs (one per line)",
    )
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="asc",
        help="Sort messages by created time in ascending or descending order (default: asc)",
    )
    parser.add_argument(
        "--no-textual",
        action="store_true",
        help="Disable Textual TUI output even when textual is installed",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw message JSON as it is fetched (requires --no-textual)",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=0,
        help="Maximum number of pages to fetch per conversation (0 means no limit)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    need_token = not bool(args.token)
    has_ids = bool(args.ids) or bool(args.ids_file)

    if args.raw and not args.no_textual:
        print("error: --raw requires --no-textual", file=sys.stderr)
        return 1

    if (need_token or not has_ids) and args.no_textual:
        if need_token:
            print("error: --token is required when Textual mode is disabled", file=sys.stderr)
        if not has_ids:
            print("error: at least one conversation ID or --ids-file is required when Textual mode is disabled", file=sys.stderr)
        return 1

    if need_token or not has_ids:
        result = run_textual_app(
            [],
            prompt_only=True,
            preset_token=args.token,
            order=args.order,
        )
        if isinstance(result, dict):
            token = str(result.get("token", "")).strip()
            ids = [str(item).strip() for item in result.get("ids", []) if str(item).strip()]
            if result.get("order") in ("asc", "desc"):
                args.order = str(result["order"])
            args.token = token
            args.ids = ids
            args.ids_file = None
        elif isinstance(result, int):
            return result
        else:
            return 0

    if not args.token:
        print("error: no access token provided", file=sys.stderr)
        return 1

    if not args.ids and not args.ids_file:
        print("error: no conversation IDs provided", file=sys.stderr)
        return 1

    sent_anything = False
    textual_ids: list[str] = []
    use_textual = not args.no_textual
    if args.raw:
        use_textual = False
    for conversation_id in iter_conversation_ids(args):
        sent_anything = True
        print(f"conversation {conversation_id}:")

        if use_textual:
            textual_ids.append(conversation_id)
            print("  load details in Textual UI…")
            continue

        yielded_any = False
        for data in iter_enriched_messages(
            conversation_id,
            args.token,
            args.order,
            args.page_limit,
        ):
            yielded_any = True
            if args.raw:
                serialized = json.dumps(data, sort_keys=True)
                print(f"  {serialized}")
                continue

            message_id = data.get("id")
            message_text = extract_message_text(data)
            sender_username = None
            sender = data.get("from")
            if isinstance(sender, dict):
                sender_username = sender.get("username") or sender.get("id")
            label = sender_username or message_id or "unknown"
            if message_text:
                print(f"  {label}: {message_text}")
            else:
                print(f"  {label} (raw):")
                formatted = json.dumps(data, indent=2, sort_keys=True)
                for line in formatted.splitlines():
                    print(f"    {line}")

        if not yielded_any:
            print("  (no messages retrieved)")

    if not sent_anything:
        print("warning: no conversation IDs provided", file=sys.stderr)
        return 1

    if use_textual and textual_ids:
        result = run_textual_app(
            textual_ids,
            token=args.token,
            order=args.order,
        )
        if isinstance(result, int):
            return result
        return 0
    return 0


def format_timestamp(value: Optional[str]) -> str:
    dt = parse_created_time(value)
    if dt is None:
        return value or ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def run_textual_app(
    conversations: list[str],
    *,
    token: Optional[str] = None,
    prompt_only: bool = False,
    preset_token: Optional[str] = None,
    order: str = "asc",
) -> int | dict[str, object] | None:
    """Render Textual UI for displaying conversations or capturing input."""
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import (
            Button,
            DataTable,
            Footer,
            Header,
            Input,
            Static,
            TabPane,
            TabbedContent,
        )
    except ImportError:
        print(
            "error: textual support requires the 'textual' package. Install it via 'pip install textual'",
            file=sys.stderr,
        )
        return 1

    class PromptApp(App):
        CSS = """
        Screen {
            align: center middle;
        }

        #container {
            width: 60%;
            max-width: 80;
            border: solid gray;
            padding: 2 3;
        }

        Input {
            margin: 1 0;
        }

        #error {
            color: red;
            min-height: 1;
        }
        """

        BINDINGS = [("escape", "app.exit", "Quit")]

        def __init__(self, preset: Optional[str]) -> None:
            super().__init__()
            self._preset = preset or ""
            self.token_input: Input
            self.ids_input: Input
            self.error_label: Static

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Vertical(id="container"):
                yield Static("Enter your Instagram Graph API token and conversation IDs (comma separated).")
                self.token_input = Input(value=self._preset, placeholder="Access token", password=True)
                yield self.token_input
                self.ids_input = Input(placeholder="Conversation IDs (comma separated)")
                yield self.ids_input
                self.error_label = Static("", id="error")
                yield self.error_label
                yield Button("Fetch Messages", id="submit", variant="success")
            yield Footer()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "submit":
                self._attempt_submit()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input is self.ids_input:
                self._attempt_submit()

        def _attempt_submit(self) -> None:
            token = self.token_input.value.strip()
            ids_raw = self.ids_input.value.strip()
            ids = [piece.strip() for piece in ids_raw.split(",") if piece.strip()]
            if not token:
                self.error_label.update("Please provide an access token.")
                self.token_input.focus()
                return
            if not ids:
                self.error_label.update("Please provide at least one conversation ID.")
                self.ids_input.focus()
                return
            self.exit({"token": token, "ids": ids, "order": order})

    class ConversationApp(App):
        CSS = """
        Screen {
            layout: vertical;
        }

        #title {
            margin: 1 2;
            text-style: bold;
        }

        DataTable {
            height: 1fr;
        }

        #pager-controls {
            padding: 0 2 1 2;
            align: left middle;
        }

        .info-label {
            padding-left: 2;
        }
        """

        def __init__(self) -> None:
            super().__init__()
            self._conversation_ids = conversations
            self._pagers = {
                conversation_id: ConversationPager(
                    conversation_id,
                    token,
                    order=order,
                    page_limit=0,
                )
                for conversation_id in conversations
            }

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            if len(self._conversation_ids) == 0:
                yield Static("No conversations provided", id="title")
            elif len(self._conversation_ids) == 1:
                conversation_id = self._conversation_ids[0]
                yield ConversationPanel(conversation_id, self._pagers[conversation_id])
            else:
                with TabbedContent():
                    for conversation_id in self._conversation_ids:
                        label = conversation_id[:16] + ("…" if len(conversation_id) > 16 else "")
                        with TabPane(label, id=f"conv-{conversation_id}"):
                            yield ConversationPanel(conversation_id, self._pagers[conversation_id])
            yield Footer()

    class ConversationPanel(Vertical):
        def __init__(self, conversation_id: str, pager: ConversationPager) -> None:
            super().__init__(id=f"panel-{conversation_id}")
            self._conversation_id = conversation_id
            self._pager = pager
            self._table: DataTable
            self._prev_button: Button
            self._next_button: Button
            self._info_label: Static
            self._current_page: Optional[ConversationPage] = None
            self._current_key: Optional[str] = None
            self._page_numbers: dict[str, int] = {}
            self._history_links: dict[str, Optional[str]] = {}

        def compose(self) -> ComposeResult:
            yield Static(f"Conversation: {self._conversation_id}", id="title")
            self._table = DataTable(zebra_stripes=True)
            self._table.add_columns("Time", "Sender", "Message")
            yield self._table
            with Horizontal(id="pager-controls"):
                self._prev_button = Button("Prev Page", id=f"prev-{self._conversation_id}")
                yield self._prev_button
                self._next_button = Button("Next Page", id=f"next-{self._conversation_id}")
                yield self._next_button
                self._info_label = Static("", classes="info-label")
                yield self._info_label

        def on_mount(self) -> None:
            self._prev_button.disabled = True
            self._next_button.disabled = True
            self._prev_button.styles.margin_right = 1
            self._info_label.update("Loading…")
            self._load_page(None)

        def _load_page(self, page_url: Optional[str], *, direction: Optional[str] = None) -> None:
            previous_key = self._current_key
            page = self._pager.fetch_page(page_url)
            if page is None:
                if direction == "next":
                    self._info_label.update("Reached the end or page limit.")
                    self._next_button.disabled = True
                elif direction == "previous":
                    self._info_label.update("No newer pages available.")
                    self._prev_button.disabled = True
                else:
                    self._info_label.update("No messages available.")
                return

            key = self._key_for_identifier(page.identifier)

            if previous_key is None and key not in self._history_links:
                self._history_links[key] = None
            elif direction == "next" and previous_key is not None:
                self._history_links[key] = previous_key

            if key not in self._page_numbers:
                if direction == "next" and previous_key is not None:
                    base = self._page_numbers.get(previous_key, 1)
                    self._page_numbers[key] = base + 1
                elif direction == "previous" and previous_key is not None:
                    base = self._page_numbers.get(previous_key, 1)
                    self._page_numbers[key] = max(base - 1, 1)
                else:
                    self._page_numbers[key] = 1

            self._current_page = page
            self._current_key = key
            prev_key = self._history_links.get(key)
            self._prev_button.disabled = prev_key is None
            self._next_button.disabled = page.next_url is None

            page_number = self._page_numbers[key]
            self._populate_table(page.messages, page_number)

        def _populate_table(self, messages: list[dict], page_number: int) -> None:
            self._table.clear()
            if not messages:
                self._info_label.update(f"Page {page_number} (no messages)")
                return

            for data in messages:
                message_text = extract_message_text(data) or ""
                sender = data.get("from")
                if isinstance(sender, dict):
                    sender_label = sender.get("username") or sender.get("id") or ""
                else:
                    sender_label = ""
                time_label = format_timestamp(data.get("created_time"))
                self._table.add_row(time_label, sender_label, message_text)
            self._table.scroll_home()
            self._info_label.update(f"Page {page_number}")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button is self._next_button and self._current_page:
                self._info_label.update("Loading…")
                self._load_page(self._current_page.next_url, direction="next")
            elif event.button is self._prev_button and self._current_page:
                if self._current_key is None:
                    self._info_label.update("No newer pages available.")
                    self._prev_button.disabled = True
                    return
                prev_key = self._history_links.get(self._current_key)
                if prev_key is None:
                    self._info_label.update("No newer pages available.")
                    self._prev_button.disabled = True
                    return
                target_url = self._identifier_from_key(prev_key)
                self._info_label.update("Loading…")
                self._load_page(target_url, direction="previous")

        @staticmethod
        def _key_for_identifier(identifier: Optional[str]) -> str:
            return identifier or "__root__"

        @staticmethod
        def _identifier_from_key(key: Optional[str]) -> Optional[str]:
            if not key or key == "__root__":
                return None
            return key

    if prompt_only:
        result = PromptApp(preset_token).run()
        return result

    if not token:
        print("error: textual display requires an access token", file=sys.stderr)
        return 1

    ConversationApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
