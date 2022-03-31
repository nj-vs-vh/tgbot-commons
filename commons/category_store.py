from dataclasses import dataclass
from datetime import timedelta
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from telebot.callback_data import CallbackData
from telebot import TeleBot
from redis import Redis

from telebot.types import User
from typing import List, Optional, Union, Dict, Any

from commons.language_store import Language, LanguageStore
from commons.callback_data_filter import setup_callback_data_filter


@dataclass
class Category:
    id_: int
    name: str
    button_caption: Union[str, Dict[Language, str]]
    hidden: bool = False


class CategoryStore:
    def __init__(
        self,
        bot_prefix: str,
        categories: List[Category],
        redis: Redis,
        category_expiration_time: timedelta = timedelta(days=15),
        language_store: Optional[LanguageStore] = None,
    ):
        self.prefix = bot_prefix
        self.redis = redis
        self.category_expiration_time = category_expiration_time
        self.categories = categories
        self.categories_by_id = {c.id_: c for c in categories}
        self.select_category_callback_data = CallbackData("cat_id", prefix="category")
        self.language_store = language_store
        if language_store is not None:  # is languages are used, assert that for every category every lang is specified
            for cat in categories:
                for lang in language_store.languages:
                    assert isinstance(cat.button_caption, dict)
                    assert lang in cat.button_caption

    def category_for_user_key(self, user_id: int) -> str:
        return f"{self.prefix}-category-of-{user_id}"

    def save_category_for_user(self, user_id: int, cat_id: int):
        key = self.category_for_user_key(user_id)
        self.redis.set(key, str(cat_id))
        self.redis.expire(key, self.category_expiration_time)

    def get_user_category(self, user_id: int) -> Optional[Category]:
        cat_id = self.redis.get(self.category_for_user_key(user_id))
        if cat_id is None:
            return None
        try:
            return self.categories_by_id.get(int(cat_id))
        except Exception:
            return None

    def setup(self, bot: TeleBot):
        setup_callback_data_filter(bot)

        @bot.callback_query_handler(func=None, callback_data=self.select_category_callback_data)
        def category_select_btn_pressed(call: CallbackQuery):
            data = self.select_category_callback_data.parse(call.data)
            user_id = call.from_user.id
            cat_id = int(data["cat_id"])
            self.save_category_for_user(user_id, cat_id)
            try:
                bot.edit_message_reply_markup(user_id, call.message.id, reply_markup=self.markup(call.from_user))
                bot.answer_callback_query(call)
            except Exception:
                # exceptions are raised when user clicks on the same button and markup is not changed
                pass

    def markup(self, for_user: User) -> InlineKeyboardMarkup:
        if self.language_store is not None:
            lang = self.language_store.get_user_lang(for_user)
        else:
            lang = None

        current_category = self.get_user_category(for_user.id)

        def get_caption(cat: Category) -> str:
            if lang is not None:
                caption = cat.button_caption[lang]
            else:
                caption: str = cat.button_caption
            if cat == current_category:
                caption = "✅ " + caption
            return caption

        return InlineKeyboardMarkup(
            keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_caption(category),
                        callback_data=self.select_category_callback_data.new(cat_id=category.id_),
                    )
                ]
                for category in self.categories
                if not category.hidden
            ]
        )
