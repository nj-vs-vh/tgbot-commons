from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from itertools import chain
from typing import Callable, Dict, List, Optional

from redis import Redis
from telebot import TeleBot, util
from telebot.types import Message, User

from commons.banned_users_store import BannedUsersStore
from commons.category_store import CategoryStore
from commons.is_in_chat import is_in_chat
from commons.language_store import Language, LanguageStore
from commons.storable import Storable
from commons.times import (GROUPED_MESSAGES_MAX_SEPARATION_TIME,
                           LARGE_EXPIRATION_TIME)


@dataclass
class AntiSpamConfig:
    throttle_after_messages: int
    throttle_duration_min: float
    soft_ban_after_throttle_violations: int
    soft_ban_duration_days: float


@dataclass
class ServiceMessages:
    # messages to user:
    forwarded_to_admin_ok: Optional[str] = "Переслано!"
    select_category: str = "Пожалуйста, сначала выберите/обновите тему сообщения, и затем пришлите его заново"
    throttling_template: str = "⚠️ Пожалуйста, не присылайте больше {} сообщений в течение {} мин!"
    # messages in admin chat
    copied_to_user_ok: str = "Скопировано!"
    message_log_unavailable: str = "История сообщений недоступна"
    somethings_wrong_template: str = "Что-то пошло не так: {}"
    unsupported_type_in_admin_reply_pre: str = "Бот поддерживает только следующие типы вложений в ответ: "

    def throttling(self, anti_spam: AntiSpamConfig) -> str:
        return self.throttling_template.format(anti_spam.throttle_after_messages, anti_spam.throttle_duration_min)

    def somethings_wrong(self, e: Exception) -> str:
        return self.somethings_wrong_template.format(e)

    def unsupported_type_in_admin_reply(self, supported_types: List[str]) -> str:
        return self.unsupported_type_in_admin_reply_pre + ", ".join(supported_types)


class AcceptMessage(Enum):
    NOW = 0
    SOON = 1
    LATER = 2


@dataclass
class HashtagMessageData(Storable):
    """Can't just store message ids as ints because we need to update hashatag messages sometimes!"""

    message_id: int
    hashtags: List[str]


def add_feedback_handlers(
    bot: TeleBot,
    admin_chat_id: int,
    bot_prefix: str,
    redis: Redis,
    hashtag_messages_in_admin_chat: bool = False,
    send_log_to_admin_chat: bool = True,
    category_store: Optional[CategoryStore] = None,
    force_category_selection: bool = False,
    service_messages: ServiceMessages = ServiceMessages(),
    language_store: Optional[LanguageStore] = None,
    service_messages_by_lang: Optional[Dict[Language, ServiceMessages]] = None,
    anti_spam: Optional[AntiSpamConfig] = None,
    banned_users_store: Optional[BannedUsersStore] = None,
    custom_user_message_filter: Optional[Callable[[Message], bool]] = None,
):
    logger = logging.getLogger(__name__ + "." + bot_prefix)

    if banned_users_store is None:
        banned_users_store = BannedUsersStore(bot_prefix, redis)

    # forwarded messages storage: backreference to origin chat, message log with user, all messages related to user

    def _origin_chat_key(forwarded_message_id: int):
        return f"{bot_prefix}-origin-chat-for-{forwarded_message_id}"

    def _messages_related_to_user_key(chat_id: int) -> str:
        return f"{bot_prefix}-forwarded-from-{chat_id}"  # legacy key name

    def _message_log_with_user_key(chat_id: int) -> str:
        return f"{bot_prefix}-log_with-{chat_id}"

    def save_message_related_to_user(user_chat_id: int, message_id: int):
        """Related to user = is deleted when user is banned"""
        key = _messages_related_to_user_key(user_chat_id)
        redis.rpush(key, str(message_id))
        redis.expire(key, LARGE_EXPIRATION_TIME)

    def save_to_message_log(origin_chat_id: int, message_id: int):
        """Message log - all user's messages and admin's responses to them"""
        key = _message_log_with_user_key(origin_chat_id)
        redis.rpush(key, str(message_id))
        redis.expire(key, LARGE_EXPIRATION_TIME)

    def save_origin_chat_for_message(forwarded_message_id: int, origin_chat_id: int):
        redis.set(
            _origin_chat_key(forwarded_message_id),
            str(origin_chat_id),
            ex=LARGE_EXPIRATION_TIME,
        )

    def save_forwarded_message(original_message: Message, forwarded_message: Message):
        """Directly forwarded from user, used to respond to chat with user"""
        origin_chat_id = original_message.chat.id
        save_origin_chat_for_message(forwarded_message.id, origin_chat_id)
        save_message_related_to_user(origin_chat_id, forwarded_message.id)
        save_to_message_log(origin_chat_id, forwarded_message.id)

    def get_messages_related_to_user(user_id: int) -> List[int]:
        return [int(id_str) for id_str in redis.lrange(_messages_related_to_user_key(user_id), 0, -1)]

    def get_message_log(origin_chat_id: int) -> List[int]:
        return [int(msgid) for msgid in redis.lrange(_message_log_with_user_key(origin_chat_id), 0, -1)]

    def get_origin_chat(forwarded_msg_id: int) -> Optional[int]:
        try:
            return int(redis.get(_origin_chat_key(forwarded_msg_id)))
        except Exception as e:
            return None

    # rate-limiting stuff

    def _message_counter_key(user_id: int) -> str:
        return f"{bot_prefix}-counter-for-user-{user_id}"

    def _rate_limit_violations_key(user_id: int) -> str:
        return f"{bot_prefix}-rate-limit-violations-{user_id}"

    def accept_message_from(user_id: int) -> AcceptMessage:
        if anti_spam is None:
            return AcceptMessage.NOW
        violations_key = _rate_limit_violations_key(user_id)
        violations = redis.get(violations_key)
        if violations is not None and int(violations) >= anti_spam.soft_ban_after_throttle_violations:
            return AcceptMessage.LATER

        counter_key = _message_counter_key(user_id)
        current_count = redis.incr(counter_key)
        redis.expire(counter_key, timedelta(minutes=anti_spam.throttle_duration_min))
        if current_count <= anti_spam.throttle_after_messages:
            return AcceptMessage.NOW
        else:
            redis.incr(violations_key)
            redis.expire(violations_key, timedelta(days=anti_spam.soft_ban_duration_days))
            return AcceptMessage.SOON

    # hashtag messages - "titles" for forwarded messages for easier admin chat navigation

    def _hashtag_message_for_msg(forwarded_msg_id: int) -> str:
        return f"{bot_prefix}-hashtag-msg-for-fwd-{forwarded_msg_id}"

    def save_hashtag_msg_data_for_message(forwarded_msg_id: int, hashtag_message_data: HashtagMessageData):
        redis.set(
            _hashtag_message_for_msg(forwarded_msg_id),
            hashtag_message_data.to_store(),
            ex=LARGE_EXPIRATION_TIME,
        )

    def get_hashtag_msg_data_for_message(
        forwarded_msg_id: int,
    ) -> Optional[HashtagMessageData]:
        try:
            return HashtagMessageData.from_store(redis.get(_hashtag_message_for_msg(forwarded_msg_id)))
        except Exception:
            return None

    def _recent_hashtag_for_user_key(user_id: int) -> str:
        return f"{bot_prefix}-recent-hashtag-for-{user_id}"

    def save_recent_hashtag_message_for_user(user_id: int, hashtag_message_data: HashtagMessageData):
        redis.set(
            _recent_hashtag_for_user_key(user_id),
            hashtag_message_data.to_store(),
            ex=GROUPED_MESSAGES_MAX_SEPARATION_TIME,
        )

    def get_recent_hashatag_message_for_user(
        user_id: int,
    ) -> Optional[HashtagMessageData]:
        try:
            return HashtagMessageData.from_store(redis.get(_recent_hashtag_for_user_key(user_id)))
        except Exception:
            return None

    UNANSWERED_HASHTAG = "#неотвечено"

    user_message_filter = custom_user_message_filter or (lambda m: True)

    @bot.message_handler(
        func=user_message_filter,
        chat_types=["private"],
        content_types=util.content_type_media,
    )
    def user_to_bot(message: Message):
        if banned_users_store.is_banned(message.from_user.id):
            return
        accept_message = accept_message_from(message.from_user.id)
        if accept_message is AcceptMessage.LATER:
            return

        if language_store is not None and service_messages_by_lang is not None:
            lang = language_store.get_user_lang(message.from_user)
            service_messages_for_user = service_messages_by_lang[lang]
        else:
            service_messages_for_user = service_messages
        if accept_message is AcceptMessage.SOON:
            bot.reply_to(message, service_messages_for_user.throttling(anti_spam))
            return

        if hashtag_messages_in_admin_chat:
            category_hashtag = None  # sentinel
            if category_store is not None:
                category = category_store.get_user_category(message.from_user.id)
                if category is None:
                    if force_category_selection:
                        bot.reply_to(
                            message,
                            service_messages_for_user.select_category,
                            reply_markup=category_store.markup(message.from_user),
                        )
                        return
                else:
                    category_hashtag = "#" + category.name

            recent_hashtag_msg_data = get_recent_hashatag_message_for_user(message.from_user.id)
            if recent_hashtag_msg_data is None or (  # send a new hashtag message?
                category_hashtag is not None and category_hashtag not in recent_hashtag_msg_data.hashtags
            ):
                hashtags = [UNANSWERED_HASHTAG]
                if category_hashtag is not None:
                    hashtags.append(category_hashtag)
                hashtag_msg = bot.send_message(admin_chat_id, " ".join(hashtags))
                # we'll delete hashtag message if the user is banned
                save_message_related_to_user(message.from_user.id, hashtag_msg.id)
                recent_hashtag_msg_data = HashtagMessageData(hashtag_msg.id, hashtags)
                save_recent_hashtag_message_for_user(message.from_user.id, recent_hashtag_msg_data)
        forwarded_msg = bot.forward_message(chat_id=admin_chat_id, from_chat_id=message.chat.id, message_id=message.id)
        save_forwarded_message(message, forwarded_msg)
        if hashtag_messages_in_admin_chat:
            # here recent_hashtag_msg_data may be just sent or already existing
            save_hashtag_msg_data_for_message(forwarded_msg.id, recent_hashtag_msg_data)
        if service_messages_for_user.forwarded_to_admin_ok is not None:
            bot.reply_to(message, service_messages_for_user.forwarded_to_admin_ok)

    is_in_admin_chat = is_in_chat(admin_chat_id)

    SUPPORTED_CONTENT_TYPES = ["text", "sticker", "document", "photo"]

    def copy_message_to_chat(chat_id: int, message: Message):
        if message.content_type == "text":
            bot.send_message(chat_id, message.html_text, parse_mode="HTML")
        elif message.content_type == "sticker":
            bot.send_sticker(chat_id, sticker=message.sticker.file_id)
        elif message.content_type == "document":
            bot.send_document(chat_id, document=message.document.file_id, caption=message.html_caption)
        elif message.content_type == "photo":
            bot.send_photo(chat_id, photo=message.photo[0].file_id, caption=message.html_caption)

    def admin_chat_message_filter(message: Message) -> bool:
        return is_in_admin_chat(message) and message.reply_to_message is not None

    def remove_unanswered_hashtag(message_id: int):
        hashtag_message_data = get_hashtag_msg_data_for_message(message_id)
        if hashtag_message_data is not None and UNANSWERED_HASHTAG in hashtag_message_data.hashtags:
            hashtag_message_data.hashtags.remove(UNANSWERED_HASHTAG)
            try:
                if hashtag_message_data.hashtags:
                    bot.edit_message_text(
                        text=" ".join(hashtag_message_data.hashtags),
                        chat_id=admin_chat_id,
                        message_id=hashtag_message_data.message_id,
                    )
                else:
                    bot.delete_message(
                        chat_id=admin_chat_id,
                        message_id=hashtag_message_data.message_id,
                    )
            except Exception as e:
                # when replying on a message in a group that has already been responded to,
                # telegram API returns and error if there's nothing to change
                logger.info(f"Error updating hashtag message: {e}")
                pass
            save_hashtag_msg_data_for_message(message_id, hashtag_message_data)

    @bot.message_handler(func=admin_chat_message_filter, content_types=util.content_type_media)
    def admin_to_bot(message: Message):
        try:
            forwarded_msg = message.reply_to_message
            origin_chat_id = get_origin_chat(forwarded_msg.id)
            if origin_chat_id is None:
                return
            if message.content_type not in SUPPORTED_CONTENT_TYPES:
                bot.reply_to(
                    message,
                    service_messages.unsupported_type_in_admin_reply(SUPPORTED_CONTENT_TYPES),
                )
                return
            if message.text == "/ban":
                banned_users_store.ban_user(origin_chat_id)
                for message_id in chain(get_messages_related_to_user(origin_chat_id), [message.id]):
                    try:
                        bot.delete_message(chat_id=admin_chat_id, message_id=message_id)
                    except Exception:
                        pass
            elif message.text == "/log":
                log_message_ids = get_message_log(origin_chat_id)
                if not log_message_ids:
                    bot.reply_to(message, service_messages.message_log_unavailable)
                    return
                send_log_to = admin_chat_id if send_log_to_admin_chat else message.from_user.id
                for message_id in log_message_ids:
                    try:
                        log_msg = bot.forward_message(
                            chat_id=send_log_to,
                            from_chat_id=admin_chat_id,
                            message_id=message_id,
                        )
                        if send_log_to_admin_chat:
                            # to be able to reply to them
                            save_origin_chat_for_message(log_msg.id, origin_chat_id)
                    except Exception:
                        pass
            else:
                copy_message_to_chat(origin_chat_id, message)
                save_to_message_log(origin_chat_id, message.id)
                bot.reply_to(message, service_messages.copied_to_user_ok)
                if hashtag_messages_in_admin_chat:
                    remove_unanswered_hashtag(forwarded_msg.id)
        except Exception as e:
            bot.reply_to(message, service_messages.somethings_wrong(e))
            logger.warning(f"Unexpected error while replying to forwarded msg: {e}")
