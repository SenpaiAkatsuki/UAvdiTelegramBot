from __future__ import annotations

from typing import Sequence

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

"""
Reply keyboards.

Contains user-library reply keyboards (topics/articles) with two-column layout.
"""

LIBRARY_REPLY_PREV_PAGE = "Попередня"
LIBRARY_REPLY_NEXT_PAGE = "Наступна"
LIBRARY_REPLY_BACK_TO_TOPICS = "До тем"
LIBRARY_REPLY_BACK_TO_MENU = "До меню"


def _two_column_rows(labels: Sequence[str]) -> list[list[KeyboardButton]]:
    # Build reply-keyboard rows with 2 buttons per row.
    rows: list[list[KeyboardButton]] = []
    current_row: list[KeyboardButton] = []
    for label in labels:
        current_row.append(KeyboardButton(text=label))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    return rows


def library_topics_reply_keyboard(
    *,
    topic_labels: Sequence[str],
    has_prev: bool,
    has_next: bool,
) -> ReplyKeyboardMarkup:
    # Topics navigation keyboard for reply-mode library.
    rows = _two_column_rows(topic_labels)
    nav_row: list[KeyboardButton] = []
    if has_prev:
        nav_row.append(KeyboardButton(text=LIBRARY_REPLY_PREV_PAGE))
    if has_next:
        nav_row.append(KeyboardButton(text=LIBRARY_REPLY_NEXT_PAGE))
    if nav_row:
        rows.append(nav_row)
    rows.append([KeyboardButton(text=LIBRARY_REPLY_BACK_TO_MENU)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
    )


def library_articles_reply_keyboard(
    *,
    article_labels: Sequence[str],
    has_prev: bool,
    has_next: bool,
) -> ReplyKeyboardMarkup:
    # Articles navigation keyboard for reply-mode library.
    rows = _two_column_rows(article_labels)
    nav_row: list[KeyboardButton] = []
    if has_prev:
        nav_row.append(KeyboardButton(text=LIBRARY_REPLY_PREV_PAGE))
    if has_next:
        nav_row.append(KeyboardButton(text=LIBRARY_REPLY_NEXT_PAGE))
    if nav_row:
        rows.append(nav_row)
    rows.append(
        [
            KeyboardButton(text=LIBRARY_REPLY_BACK_TO_TOPICS),
            KeyboardButton(text=LIBRARY_REPLY_BACK_TO_MENU),
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
    )
