import discord
import os
from dotenv import load_dotenv
from leaderboard.rbb import RbbLeaderboard
from common import logger
from rcon_trackers.rbb_tracker import RbbTracker
from rcon_trackers.game_events import GameEventsTracker

logger.use_date_time_logger()

load_dotenv()

config_bot_channel_id_raw = os.environ.get("CONFIG_BOT_CHANNEL", "")
logger.info(f"LOADING CONFIG BOT CHANNEL {config_bot_channel_id_raw}")
admin_id = os.environ.get("ADMIN_FFID", None)

logger.info(f"LOADING ADMINID {admin_id}")
if not admin_id:
    raise Exception("ADMIN ID NOT LOADED")

ptero_api_token = os.environ.get("PTERO_API_KEY", "")
ptero_server_id = os.environ.get("PTERO_SERVER_ID", "")
ptero_file_path = os.environ.get("PTERO_FILE_PATH", "")

if not ptero_api_token:
    raise Exception("PTERO_API_KEY not defined in environment")
if not ptero_server_id:
    raise Exception("PTERO_SERVER_ID not defined in environment")
if not ptero_file_path:
    raise Exception("PTERO_FILE_PATH not defined in environment")


CONFIG_BOT_CHANNEL_ID = (
    int(config_bot_channel_id_raw) if config_bot_channel_id_raw.isnumeric() else 0
)


bot = discord.Bot()


rbb_leaderboard = RbbLeaderboard(bot)


game_tracker = GameEventsTracker(bot, CONFIG_BOT_CHANNEL_ID)

bar_fight = RbbTracker(
    bot,
    CONFIG_BOT_CHANNEL_ID,
    admin_id,
    api_token=ptero_api_token,
    server_id=ptero_server_id,
    file_path=ptero_file_path,
)
bot.add_cog(game_tracker)
bot.add_cog(rbb_leaderboard)
bot.add_cog(bar_fight)
bot.run(os.environ["D_TOKEN"])
