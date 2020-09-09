import logging
import asyncio
import achallonge

from abc import ABC
from typing import Mapping
from laggron_utils.logging import close_logger

from redbot.core import commands
from redbot.core import Config
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n

from .dataclass import Tournament, Match, ChallongeTournament
from .games import Games
from .registration import Registration
from .settings import Settings
from .streams import Streams

log = logging.getLogger("red.laggron.tournaments")
_ = Translator("Tournaments", __file__)


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass

    Credit to https://github.com/Cog-Creators/Red-DiscordBot (mod cog) for all mixin stuff.
    """

    pass


@cog_i18n(_)
class Tournaments(
    Games, Registration, Settings, Streams, commands.Cog, metaclass=CompositeMetaClass
):

    default_guild_settings = {
        "credentials": {"username": None, "api": None},  # challonge login info
        "current_phase": None,  # possible values are "setup", "register", "checkin", "run"
        "delay": 10,
        "register": {"opening": 0, "closing": 10},
        "checkin": {"opening": 60, "closing": 15},
        "start_bo5": 0,
        "channels": {
            "announcements": None,
            "category": None,
            "checkin": None,
            "queue": None,
            "register": None,
            "scores": None,
            "stream": None,
            "to": None,
        },
        "roles": {"participant": None, "streamer": None, "to": None},
        "tournament": {
            "name": None,
            "game": None,
            "url": None,
            "id": None,
            "limit": None,
            "status": None,
            "tournament_start": None,
            "bot_prefix": None,
            "participants": [],
            "matches": [],
            "winner_categories": [],
            "loser_categories": [],
            "phase": None,
            "type": None,
        },
    }

    default_game_settings = {
        "ruleset": None,
        "role": None,
        "baninfo": None,
        "ranking": {"league_name": None, "league_id": None},
        "stages": [],
        "counterpicks": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.data = Config.get_conf(cog_instance=self, identifier=260, force_registration=True)
        self.tournaments: Mapping[int, Tournament] = {}

        self.data.register_guild(**self.default_guild_settings)
        self.data.init_custom("GAME", 2)  # guild ID > game name
        self.data.register_custom("GAME", **self.default_game_settings)

    async def restore_tournaments(self):
        count = 0
        log.debug(f"Resuming tournaments...")
        for guild_id, data in (await self.data.all_guilds()).items():
            if data["tournament"]["name"] is None:
                continue
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            try:
                game_data = await self.data.custom(
                    "GAME", guild_id, data["tournament"]["game"]
                ).all()
                if data["tournament"]["tournament_type"] == "challonge":
                    credentials = await self.data.guild_from_id(guild_id).credentials()
                    if any([x is None for x in credentials.values()]):
                        log.warning(
                            f"[Guild {guild_id}] Credentials not found, "
                            "not resuming the tournament."
                        )
                        continue
                    achallonge.set_credentials(credentials["username"], credentials["api"])
                    data.update(game_data)
                    tournament = ChallongeTournament.from_saved_data(
                        guild, self.data, data["tournament"], data
                    )
                    self.tournaments[guild_id] = tournament
                    if tournament.phase == "ongoing":
                        tournament.start_loop_task()
            except Exception as e:
                log.error(f"[Guild {guild_id}] Failed to resume tournament.", exc_info=e)
            else:
                count += 1
        if count > 0:
            log.info(f"Resumed {count} tournaments.")
        else:
            log.info(f"No tournament had to be resumed.")

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        try:
            error = error.original
        except AttributeError:
            pass
        if isinstance(error, achallonge.ChallongeException):
            await ctx.send(
                _(
                    "Erreur de Challonge: {error}\n"
                    "Si ce problème persiste, contactez les T.O. ou un administrateur du bot."
                ).format(error=error.args[0])
            )
        else:
            await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    def cog_unload(self):
        log.debug("Unloading cog...")

        # remove all handlers from the logger, this prevents adding
        # multiple times the same handler if the cog gets reloaded
        close_logger(log)

        # cancel all pending tasks
        def cancel(task: asyncio.Task):
            if task is not None:
                task.cancel()

        tournament: Tournament
        match: Match
        for tournament in self.tournaments.values():
            cancel(tournament.loop_task)
            for match in tournament.matches:
                cancel(match.timeout_task)
                cancel(match.deletion_task)
