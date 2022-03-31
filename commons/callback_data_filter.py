from telebot.custom_filters import AdvancedCustomFilter
from telebot.callback_data import CallbackData
from telebot import types, TeleBot


class CallbackDataFilter(AdvancedCustomFilter):
    key = 'callback_data'

    def check(self, call: types.CallbackQuery, callback_data: CallbackData):
        try:
            callback_data.parse(call.data)
            return True
        except ValueError:
            return False


def setup_callback_data_filter(bot: TeleBot):
    """Allows filtering based on CallbackData instances with e.g.

    @bot.callback_query_handler(func=None, callback_data=my_cbk_data)
    """
    bot.add_custom_filter(CallbackDataFilter())
