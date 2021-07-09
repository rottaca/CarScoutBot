import enum
import logging
import sys

import requests
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, PicklePersistence, \
    CallbackQueryHandler

try:
    from BeautifulSoup import BeautifulSoup
except ImportError:
    from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

token = sys.argv[1]
check_interval = int(sys.argv[2])

chats = {}

URLS = "URLS"
STATE = "STATE"
PAGE_TYPE = "PAGE_TYPE"
CARS_FOUND = "CARS_FOUND"
URL_PATH = "URL_PATH"


class PageTypes(enum.Enum):
    MOBILE = "mobile"
    AUTOSCOUT24 = "autoscout24"


@enum.unique
class ChatStates(enum.Enum):
    INIT = 0
    WAIT_FOR_URL = 1
    WAIT_FOR_REMOVAL = 2


def get_chat(context, chat_id):
    if "chat_data" not in context.dispatcher.chat_data[chat_id]:
        context.dispatcher.chat_data[chat_id]["chat_data"] = {
            STATE: ChatStates.INIT,
            URLS: []
        }

    return context.dispatcher.chat_data[chat_id]["chat_data"]


def remove_job_if_exists(name: str, context: CallbackContext) -> bool:
    """Remove job with given name. Returns whether job was removed."""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    print("Removed jobs.")
    return True


def get_help(update, context):
    text = "I'm your CarScout Bot! \n" \
           "Commands: \n" \
           " - /watch: Register a new search query \n" \
           " - /remove: Remove an existing search query \n" \
           " - /list: List registered queries \n"

    update.message.reply_text(text)


def get_html(url):
    headers = {
        "User-Agent": "My User Agent",
        "From": "myemail@aol.com"
    }
    data = requests.get(url, headers=headers)
    return data.content


def parse_html_mobile(html):
    parsed_html = BeautifulSoup(html, features="html.parser")
    try:
        # Find list of results
        results = parsed_html.body.find_all('div', attrs={'class': 'cBox-body--resultitem'})
        # Find unique Ids for vehicles.
        items = [r.find("a", attrs={"class": "result-item"}).attrs["data-ad-id"] for r in results]

        print(f"Number of cars found: {len(items)}")
        items = set(items)
    except KeyError:
        items = set()
        print("Unable to process url: ")
        with open("error.html", "w") as f:
            f.write(parsed_html.prettify())

    return items


def parse_html_autoscout24(html):
    parsed_html = BeautifulSoup(html, features="html.parser")
    try:
        # Find list of results
        results = parsed_html.body.find_all('div', attrs={'class': 'cl-list-element-gap'})
        # Find unique Ids for vehicles.
        items = [r.attrs["data-guid"] for r in results if "data-guid" in r.attrs]

        print(f"Number of cars found: {len(items)}")
        items = set(items)
    except KeyError:
        items = set()
        print("Unable to process url! ")
        with open("error.html", "w") as f:
            f.write(parsed_html.prettify())

    return items


def parse_html(html, page_type: PageTypes):
    if page_type == PageTypes.MOBILE:
        return parse_html_mobile(html)
    elif page_type == PageTypes.AUTOSCOUT24:
        return parse_html_autoscout24(html)
    else:
        return -1


def get_cars_from_url(url_data):
    data = str(get_html(url_data[URL_PATH]))
    return parse_html(data, url_data[PAGE_TYPE])


def build_menu(buttons, n_cols, header_buttons=None, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)
    return menu


def start_remove(update, context):
    chat_data = get_chat(context, update.effective_chat.id)

    if len(chat_data[URLS]) > 0:
        chat_data[STATE] = ChatStates.WAIT_FOR_REMOVAL
        button_list = []
        for idx, url in enumerate(chat_data[URLS]):
            txt = f"{idx + 1:<2}: {url[PAGE_TYPE].value.upper()} ({len(url[CARS_FOUND])} cars)"
            button_list.append(InlineKeyboardButton(txt, callback_data=idx))
        reply_markup = InlineKeyboardMarkup(build_menu(button_list, n_cols=1))

        context.bot.send_message(chat_id=update.message.chat_id, text='Choose from the following',
                                 reply_markup=reply_markup)
    else:
        chat_data[STATE] = ChatStates.INIT
        context.bot.send_message(chat_id=update.message.chat_id, text='No queries to remove.')


def start_watch(update, context):
    chat_data = get_chat(context, update.effective_chat.id)

    chat_data[STATE] = ChatStates.WAIT_FOR_URL
    update.message.reply_text("Please specify a search URL to watch!")


def list_func(update, context):
    chat_data = get_chat(context, update.effective_chat.id)
    chat_data[STATE] = ChatStates.INIT

    if len(chat_data[URLS]) > 0:
        txt = "You have registered these search queries:\n"
        for idx, url in enumerate(chat_data[URLS]):
            txt += f" {idx + 1:<2}: <b>{url[PAGE_TYPE].value.upper()}</b>: I found <b>{len(url[CARS_FOUND])}</b> cars. Open <a href=\"{url[URL_PATH]}\">URL</a>\n"
    else:
        txt = "No queries register. Use /watch to register a new query. Use /move to remove a new query."

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=txt,
                             parse_mode=telegram.ParseMode.HTML,
                             disable_web_page_preview=True)


def on_timeout(context: CallbackContext) -> None:
    chat_id = context.job.context
    chat_data = get_chat(context, chat_id)

    for idx, url in enumerate(chat_data[URLS]):
        print(f"Checking url {idx} from {url[PAGE_TYPE]}.")
        curr_cars = get_cars_from_url(url)

        if url[CARS_FOUND] != curr_cars:
            print(url[CARS_FOUND], curr_cars)
            context.bot.send_message(chat_id=chat_id,
                                     text=f"The list of vehicles change! Check this "
                                          f"<a href=\"{url[URL_PATH]}\">link</a>!",
                                     parse_mode=telegram.ParseMode.HTML)
            url[CARS_FOUND] = curr_cars


def detect_page_type(url):
    if ".mobile." in url:
        return PageTypes.MOBILE
    elif ".autoscout24." in url:
        return PageTypes.AUTOSCOUT24
    else:
        return None


def remove(update: Update, context: CallbackContext):
    chat_data = context.chat_data["chat_data"]

    if chat_data[STATE] != ChatStates.WAIT_FOR_REMOVAL:
        context.bot.send_message(chat_id=update.effective_chat.id, text='Invalid state.')
        return

    url_idx = update.callback_query.data
    print(f"rm query {url_idx}")
    del chat_data[URLS][int(url_idx)]

    context.bot.send_message(chat_id=update.effective_chat.id, text='Search query removed.')

    list_func(update, context)


def watch(update, context, chat_id, chat_data, url):
    if any(url == u[URL_PATH] for u in chat_data[URLS]):
        update.message.reply_text("Url already exists. Going back to idle!")
        return

    url_data = {
        URL_PATH: url,
        PAGE_TYPE: detect_page_type(url)
    }
    chat_data[URLS].append(url_data)
    curr_cars = get_cars_from_url(url_data)
    url_data[CARS_FOUND] = curr_cars

    try:
        remove_job_if_exists(str(chat_id), context)
        context.job_queue.run_repeating(on_timeout, check_interval, context=chat_id, name=str(chat_id))
        update.message.reply_text("Successfully registered. You will get notified when new cars are available! "
                                  f"Currently, your query shows {len(curr_cars)} vehicles. Checking for updates...")
    except (IndexError, ValueError):
        update.message.reply_text('Usage: /watch')


def process_text(update, context):
    chat_id = update.effective_chat.id
    chat_data = get_chat(context, chat_id)

    chat_state = chat_data[STATE]

    if chat_state == ChatStates.INIT:
        get_help(update, context)
    elif chat_state == ChatStates.WAIT_FOR_URL:
        watch(update, context, chat_id, chat_data, update.message.text)
        chat_data[STATE] = ChatStates.INIT
    elif chat_state == ChatStates.WAIT_FOR_REMOVAL:
        remove(update, context, chat_id, chat_data, update.message.text)
        chat_data[STATE] = ChatStates.INIT
    else:
        get_help(update, context)
        chat_data[STATE] = ChatStates.INIT


def unknown(update, context):
    update.message.reply_text("Sorry, I didn't understand that command.")
    get_help(update, context)


def main() -> None:
    bot = telegram.Bot(token=token)

    my_persistence = PicklePersistence(filename='mystate.pkl')

    updater = Updater(token=token, persistence=my_persistence, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('watch', start_watch))

    dispatcher.add_handler(CommandHandler('remove', start_remove))

    dispatcher.add_handler(CommandHandler('list', list_func))

    process_text_handler = MessageHandler(Filters.text & (~Filters.command), process_text)
    dispatcher.add_handler(process_text_handler)

    dispatcher.add_handler(CallbackQueryHandler(remove))

    unknown_handler = MessageHandler(Filters.command, unknown)
    dispatcher.add_handler(unknown_handler)

    for chat_id in dispatcher.chat_data:
        dispatcher.job_queue.run_repeating(on_timeout, check_interval, context=chat_id, name=str(chat_id))

    # Start the Bot
    updater.start_polling()

    # Block until you press Ctrl-C or the process receives SIGINT, SIGTERM or
    # SIGABRT. This should be used most of the time, since start_polling() is
    # non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
