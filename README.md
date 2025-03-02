# dc-lb-lite

Leaderboard management discord bot

Uses JSON as DB, performance is not a thing here.

## Setup:

Requires shell enabled terminal with docker installed

1. clone/download code
2. create a .env file in the same folder as the code, use template below
	```env
	CONFIG_BOT_CHANNEL=<CHANNEL ID IF YOU WANT TO LIMIT ADMIN COMMANDS TO A SINGLE CHANNEL>
	LEADERBOARD_CHANNEL=<CHANNEL ID FOR THE LEADERBOARD>
	D_TOKEN=<BOT TOKEN>
	```
3. run `sh restart.sh`
   1. this will execute the necessary commands to run the bot in a docker container, check the `restart.sh` file if you need to change how the bot is run (i.e. running it without docker container)