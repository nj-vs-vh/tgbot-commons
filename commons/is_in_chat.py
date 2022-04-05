import logging

from telebot.types import Message

logger = logging.getLogger(__name__)


def is_in_chat(chat_id: int):
    def chat_tester(m: Message) -> bool:
        try:
            return m.chat.id == chat_id
        except Exception as e:
            logger.error(e)
            return False

    return chat_tester
