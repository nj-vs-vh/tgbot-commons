from telebot import TeleBot
from telebot.types import Message
from redis import Redis

from commons.category_store import CategoryStore, Category


bot = TeleBot("TOKEN")
bot_prefix = "unique-prefix"
redis = Redis.from_url("redis://localhost:5050")

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


@bot.message_handler(commands=["mycategory"])
def mycat(message: Message):
    category = category_store.get_user_category(message.from_user.id)
    if category is None:
        bot.reply_to(message, "You haven't chosen category yet")
    else:
        bot.reply_to(message, f"Your category: {category.name}")


# don't forget to allow callback query updates!
bot.infinity_polling(allowed_updates=["message", "callback_query"], interval=2)
