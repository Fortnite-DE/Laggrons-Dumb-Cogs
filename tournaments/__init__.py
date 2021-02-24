import logging
import importlib.util

from redbot.core.errors import CogLoadError

dependencies = {
    "laggron_utils": "git+https://github.com/retke/Laggron-utils.git",
    "achallonge": "apychal",
    "aiofiles": "aiofiles",
}

for dependency, package in dependencies.items():
    if not importlib.util.find_spec(dependency):
        raise CogLoadError(
            f"You need the `{dependency}` package for this cog. Use the command `[p]pipinstall "
            f"{package}` or type `pip3 install -U {package}` "
            "in the terminal to install the library."
        )

from .tournaments import Tournaments
from laggron_utils import init_logger, close_logger

log = logging.getLogger("red.laggron.tournaments")


async def _save_backup(config):
    import json
    from datetime import datetime
    from redbot.core.data_manager import cog_data_path

    date = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
    path = cog_data_path(raw_name="Tournaments") / f"settings-backup-{date}.json"
    full_data = {
        "260": {
            "GUILD": await config.all_guilds(),
            "GAME": await config.custom("GAME").all(),
        }
    }
    data = json.dumps(full_data)
    with open(path.absolute(), "w") as file:
        file.write(data)
    log.info(f"Backup file saved at '{path.absolute()}', now starting conversion...")


async def _convert_to_v1(config):
    def convert_timedelta(data: dict):
        for key, value in data.items():
            if key in ("bo3", "bo5"):
                data[key] = tuple(x * 60 for x in value)
            elif key in ("channels", "roles"):
                data[key] = value
            elif isinstance(value, int):
                data[key] = value * 60  # minutes to seconds
            elif isinstance(value, dict):
                data[key] = convert_timedelta(value)
        return data

    all_games: dict = await config.custom("GAME").all()
    guilds: dict = await config.all_guilds()
    for guild_id, data in guilds.items():
        if "credentials" not in data and guild_id not in all_games:
            continue
        games: dict = all_games.get(str(guild_id), {})
        tournament = data.pop("tournament")
        if tournament and tournament["name"]:
            if len(games) > 1 and tournament["game"] in games:
                tournament["config"] = tournament["game"]
            else:
                tournament["config"] = None
        await config.guild_from_id(guild_id).set(
            {"credentials": data.pop("credentials"), "tournament": tournament}
        )
        settings = {None: data}
        if len(games) == 1:
            game = list(games.values())[0]
            settings[None]["roles"] = {"player": game.pop("role")}
            settings[None].update(game)
        else:
            for name, value in games.items():
                role = value.pop("role")
                settings[name] = value
                if role:
                    settings[name]["roles"] = {"player": role}
        settings = convert_timedelta(settings)
        if settings:
            await config.custom("SETTINGS", guild_id).set(settings)
    # Can't delete a Config group, so we empty it to save some data
    await config.custom("GAME").set({})


async def update_config(config):
    """
    Versions may require an update with the config body.
    """
    if await config.data_version() == "0.0":
        all_guilds = await config.all_guilds()
        if not any("channels" in x for x in all_guilds.values()):
            await config.data_version.set("1.0")
            return
        log.info(
            "Tournaments 1.1.0 changed the way data is stored. Your data will be updated. "
            "A copy will be created. If something goes wrong and the data is not usable, keep "
            "that file safe and ask support on how to recover the data."
        )
        # we're only registering GAME here, because the cog doesn't do that on load anymore
        config.init_custom("GAME", 2)
        config.register_custom("GAME", **{})
        # perform a backup, any exception MUST be raised
        await _save_backup(config)
        # we consider we have a safe backup at this point
        await _convert_to_v1(config)
        await config.data_version.set("1.0")
        log.info(
            "All data successfully converted! The cog will now load. Keep the backup file for "
            "a bit since problems can occur after cog load."
        )
        # phew


async def restore_tournaments(bot, cog):
    await bot.wait_until_ready()
    await cog.restore_tournaments()


async def setup(bot):
    init_logger(log, "Tournaments")
    n = Tournaments(bot)
    try:
        await update_config(n.data)
    except Exception as e:
        log.critical(
            "Cannot update config. Data can be corrupted, do not try to load the cog."
            "Contact support for further instructions.",
            exc_info=e,
        )
        close_logger(log)  # still need some cleaning up
        raise CogLoadError(
            "After an update, the cog tried to perform changes to the saved data but an error "
            "occured. Read your console output or tournaments.log (located over "
            "Red-DiscordBot/cogs/Tournaments) for more details.\n"
            "**Do not try to load the cog again until the issue is resolved, the data might be"
            "corrupted.** Contacting support is advised (Laggron's support server or official "
            "3rd party cog support server, #support_laggrons-dumb-cogs channel)."
        ) from e
    bot.add_cog(n)
    bot.loop.create_task(restore_tournaments(bot, n))
    log.debug("Cog successfully loaded on the instance.")
