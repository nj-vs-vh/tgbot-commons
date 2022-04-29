import logging

from redis import Redis
from telebot import TeleBot
from telebot.types import Message

from commons.banned_users_store import BannedUsersStore
from commons.category_store import Category, CategoryStore
from commons.feedback import AntiSpamConfig, add_feedback_handlers

logging.basicConfig(level=logging.INFO)

bot = TeleBot("TOKEN")

# attention! admin_chat_id should be int
# here it's a string just to show what these ids normally look like
admin_chat_id = "-100XXXXXXXXXX"

bot_prefix = "another-unique-prefix"
redis = Redis.from_url("redis://localhost:5050")

banned_users_store = BannedUsersStore(bot_prefix, redis)

category_store = CategoryStore(
    bot_prefix,
    [
        Category(1, name="one", button_caption="One"),
        Category(2, name="two", button_caption="Two"),
        Category(3, name="three", button_caption="Three"),
    ],
    redis,
)
category_store.setup(bot)  # adds necessary handlers to bot


@bot.message_handler(commands=["start"])
def start(message: Message):
    bot.send_message(
        message.from_user.id,
        "Please choose your category:",
        reply_markup=category_store.markup(message.from_user),
    )


add_feedback_handlers(
    bot,
    admin_chat_id,
    bot_prefix,
    redis,
    hashtag_messages_in_admin_chat=True,
    category_store=category_store,
    force_category_selection=True,
    anti_spam=AntiSpamConfig(
        throttle_after_messages=5,
        throttle_duration_min=1,
        soft_ban_after_throttle_violations=10,
        soft_ban_duration_days=1,
    ),
    banned_users_store=banned_users_store,
)


# don't forget to allow callback query updates!
bot.infinity_polling(allowed_updates=["message", "callback_query"], interval=2)
