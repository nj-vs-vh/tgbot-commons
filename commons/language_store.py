from __future__ import annotations
from asyncio.log import logger

from enum import Enum
from redis import Redis

from telebot import TeleBot
from telebot.callback_data import CallbackData
from telebot.types import (
    User,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from typing import List, Callable, Optional, Dict

from commons.callback_data_filter import setup_callback_data_filter


class Language(Enum):
    """IETF language tags: https://en.wikipedia.org/wiki/IETF_language_tag"""

    UK = "uk"
    EN = "en"
    RU = "ru"
    PL = "pl"


MultilangText = Dict[Language, str]


class LanguageStore:
    def __init__(
        self,
        bot_prefix: str,
        languages: List[Language],
        default_language: Language,
        redis: Redis,
    ):
        self.prefix = bot_prefix
        self.redis = redis
        self.languages = languages
        self.default_language = default_language
        self.language_callback_data = CallbackData("code", prefix="lang")

    def _user_language_key(self, user_id: int) -> str:
        return f"{self.prefix}-user-lang-{user_id}"

    def get_user_lang(self, user: User) -> Language:
        try:
            stored_lang_code = self.redis.get(self._user_language_key(user.id)).decode("utf-8")
            return Language(stored_lang_code)
        except Exception as e:
            logger.debug(f"Error retrieving saved lang for user: {e}")
            try:
                language_guessed = Language(user.language_code)
                assert language_guessed in self.languages, "user's lang not supported"
                return language_guessed
            except Exception as e:
                logger.debug(f"Error guessing lang for user: {e}")
                return self.default_language

    def save_user_lang(self, user: User, lang: Language):
        self.redis.set(self._user_language_key(user.id), lang.value)

    def setup(
        self,
        bot: TeleBot,
        on_language_change: Optional[Callable[[Message, User], None]] = None,
    ):
        setup_callback_data_filter(bot)

        @bot.callback_query_handler(func=None, callback_data=self.language_callback_data)
        def language_selected(call: CallbackQuery):
            data = self.language_callback_data.parse(call.data)
            user = call.from_user
            language = Language(data["code"])
            self.save_user_lang(user, language)
            try:
                bot.edit_message_reply_markup(user.id, call.message.id, reply_markup=self.markup(user))
                bot.answer_callback_query(call)
            except Exception:
                # exceptions are raised when user clicks on the same button and markup is not changed
                pass
            if on_language_change is not None:
                on_language_change(call.message, call.from_user)

    def markup(self, for_user: User) -> InlineKeyboardMarkup:
        user_lang = self.get_user_lang(for_user)

        def get_lang_text(l: Language) -> str:
            l_str = l.value.upper()
            if l is user_lang:
                l_str = "[ " + l_str + " ]"
            return l_str

        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text=get_lang_text(l),
                        callback_data=self.language_callback_data.new(code=l.value),
                    )
                    for l in self.languages
                ]
            ],
            row_width=len(self.languages),
        )
