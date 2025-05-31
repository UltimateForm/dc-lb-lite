import logging


def use_date_time_logger():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("./persist/bot.log", mode="w"),
        ],
    )


def info(msg: str):
    logging.info(msg)


def warning(msg: str):
    logging.warning(msg)


def error(msg: str | Exception):
    msg_to_log = msg
    if isinstance(msg, Exception):
        msg_to_log = str(msg)
    logging.error(msg_to_log)


def debug(msg: str):
    logging.debug(msg)
