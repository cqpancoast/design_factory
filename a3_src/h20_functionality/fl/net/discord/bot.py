# -*- coding: utf-8 -*-
"""
---

title:
    "Discord HTTP API integration support module."

description:
    "This Python module is designed to interact
    with the Discord API using a separate process
    for handling requests and responses."

id:
    "08a41d6d-9b21-4248-b87a-9f4c7a003648"

type:
    dt003_python_module

validation_level:
    v00_minimum

protection:
    k00_general

copyright:
    "Copyright 2023 William Payne"

license:
    "Licensed under the Apache License, Version
    2.0 (the License); you may not use this file
    except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed
    to in writing, software distributed under
    the License is distributed on an AS IS BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
    either express or implied. See the License
    for the specific language governing
    permissions and limitations under the
    License."

...
"""


import asyncio
import collections
import multiprocessing
import os
import queue

import fl.util


BOT_COMMAND_PREFIX = '/'


# -----------------------------------------------------------------------------
FileData   = collections.namedtuple(
                            'FileData',
                            ['filename', 'spoiler', 'description', 'buffer'])

ButtonData = collections.namedtuple(
                            'ButtonData',
                            ['label', 'id_btn'])


# -----------------------------------------------------------------------------
@fl.util.coroutine
def coro(cfg_bot):
    """
    Yield results for workflow coroutines sent to the OpenAI web API.

    Start the client in a separate process.

    """

    tup_key_required = ('str_token',)
    for str_key in tup_key_required:
        if str_key not in cfg_bot:
            raise ValueError(
                'Missing required configuration: {key}'.format(key = str_key))
    if not isinstance(cfg_bot['str_token'], str):
        raise ValueError(
                'cfg_bot["str_token"] must be a string.')
    cfg_bot['secs_sleep'] = cfg_bot.get('secs_sleep', 0.5)
    if not isinstance(cfg_bot['secs_sleep'], (int, float)):
        raise ValueError(
                'cfg_bot["secs_sleep"] must be an integer or float value.')

    str_name_process   = 'discord-bot'
    fcn_bot            = _discord_bot
    queue_msg_to_bot   = multiprocessing.Queue()  # msg system  --> discord
    queue_cmd_to_bot   = multiprocessing.Queue()  # cmd system  --> discord
    queue_msg_from_bot = multiprocessing.Queue()  # msg discord --> system
    queue_cmd_from_bot = multiprocessing.Queue()  # cmd discord --> system
    queue_log_from_bot = multiprocessing.Queue()  # log discord --> system
    tup_args           = (cfg_bot,
                          queue_msg_to_bot,
                          queue_cmd_to_bot,
                          queue_msg_from_bot,
                          queue_cmd_from_bot,
                          queue_log_from_bot)
    proc_bot           = multiprocessing.Process(
                                        target = fcn_bot,
                                        args   = tup_args,
                                        name   = str_name_process,
                                        daemon = True)  # So we get terminated
    proc_bot.start()

    list_msg_to_bot   = list()
    list_cmd_to_bot   = list()
    list_msg_from_bot = list()
    list_cmd_from_bot = list()
    list_log_from_bot = list()

    while True:

        list_msg_to_bot.clear()
        list_cmd_to_bot.clear()

        (list_msg_to_bot,
         list_cmd_to_bot) = yield (list_msg_from_bot,
                                   list_cmd_from_bot,
                                   list_log_from_bot)

        list_msg_from_bot.clear()
        list_cmd_from_bot.clear()
        list_log_from_bot.clear()

        # If the rest of the system sends us
        # any system messages or new commands
        # to configure, then forward them
        # on to the discord client process
        # to either be sent to the relevant
        # channel (in the case of messages),
        # or to use to configure new commands
        # (in the case of command configuration).
        #
        for map_msg in list_msg_to_bot:
            try:
                queue_msg_to_bot.put(map_msg, block = False)
            except queue.Full as err:
                list_log_from_bot.append(
                        'Message dropped: queue_msg_to_bot is full.')

        for cfg_cmd in list_cmd_to_bot:
            try:
                queue_cmd_to_bot.put(cfg_cmd, block = False)
            except queue.Full as err:
                list_log_from_bot.append(
                        'Command config dropped: queue_cmd_to_bot is full.')

        # Retrieve any user messages, command
        # invocations or log messages from the
        # discord client and forward them to
        # the rest of the system for further
        # processing.
        #
        while True:
            try:
                list_msg_from_bot.append(queue_msg_from_bot.get(block = False))
            except queue.Empty:
                break

        while True:
            try:
                list_cmd_from_bot.append(queue_cmd_from_bot.get(block = False))
            except queue.Empty:
                break

        while True:
            try:
                list_log_from_bot.append(queue_log_from_bot.get(block = False))
            except queue.Empty:
                break


# -----------------------------------------------------------------------------
def _discord_bot(cfg_bot,
                 queue_msg_to_bot,
                 queue_cmd_to_bot,
                 queue_msg_from_bot,
                 queue_cmd_from_bot,
                 queue_log_from_bot):
    """
    Run the discord client.

    This function is expected to be run in a separate daemon process.

    """

    import collections.abc
    import functools
    import io
    import logging
    import os

    import discord
    import discord.ext
    import discord.ext.commands

    intents                 = discord.Intents.default()
    intents.guilds          = True
    intents.dm_messages     = True
    intents.dm_reactions    = True
    intents.message_content = True
    intents.messages        = True
    intents.reactions       = True
    intents.guild_messages  = True
    bot                     = discord.ext.commands.Bot(
                                        command_prefix = BOT_COMMAND_PREFIX,
                                        intents        = intents)

    buffer_log = io.StringIO()
    loghandler = logging.StreamHandler(buffer_log)
    log        = logging.getLogger('discord')

    # -------------------------------------------------------------------------
    @bot.event
    async def on_ready():
        """
        Create worker tasks once the client is ready.

        This callback is invoked once the
        client is done preparing the data
        that has been received from Discord.
        This usually happens after login
        is successful and the Client.guilds
        and similar data structures are
        filled up.

        """

        task_msg = bot.loop.create_task(coro = _service_all_queues(
                                                        cfg_bot,
                                                        queue_msg_to_bot,
                                                        queue_cmd_to_bot,
                                                        queue_log_from_bot))

    # -------------------------------------------------------------------------
    async def _service_all_queues(cfg_bot,
                                  queue_msg_to_bot,
                                  queue_cmd_to_bot,
                                  queue_log_from_bot):
        """
        Message queue servicing coroutine.

        This coroutine is intended to
        run continuously, servicing
        the multiprocessing.queue
        instance that feeds messages
        from the rest of the system to
        the discord bot.

        This coroutine is started from
        the on_ready callback - i.e as
        soon as the discord bot is
        ready.

        """

        # This is a redundant sanity check, as
        # the service task should be created in
        # the on_ready callback.
        #
        await bot.wait_until_ready()

        count_log_attempt = 0
        map_user          = dict()
        map_chan          = dict()
        map_cmd           = dict()

        while True:

            # Try to send log data from the
            # discord bot to the rest of the
            # system. (Print to stdout if it
            # doesn't work for some reason).
            #
            str_log = buffer_log.getvalue()
            if str_log:
                if await _service_log_queue(queue_log_from_bot, str_log):
                    count_log_attempt = 0
                elif count_log_attempt < 10:
                    count_log_attempt += 1
                else:
                    print(str_log)
                    loghandler.flush()

            # Service outbound messages from the
            # system to discord, then command
            # configuration from the system to
            # discord. Don't sleep if any data
            # is ready.
            #
            do_sleep = True
            if await _service_msg_queue(queue_msg_to_bot, map_chan, map_user):
                do_sleep = False
            if await _service_cmd_queue(queue_cmd_to_bot, map_cmd):
                do_sleep = False
            if do_sleep:
                await asyncio.sleep(cfg_bot['secs_sleep'])


    # -------------------------------------------------------------------------
    async def _service_log_queue(queue_log_from_bot, str_log):
        """
        Send log data from the discord bot to the rest of the system.

        If there is any log data in the
        buffer, then enqueue it to be
        sent back to the rest of the
        system.

        """
        is_ok = False
        try:
            queue_log_from_bot.put(str_log, block = False)
        except queue.Full:
            log.error(
                'Log message dropped. ' \
                'queue_log_from_bot is full.')
        else:
            is_ok = True
            loghandler.flush()
        return is_ok


    # -------------------------------------------------------------------------
    async def _service_msg_queue(queue_msg_to_bot, map_chan, map_user):
        """
        Service outbound messages from the system to discord.

        """

        try:
            map_msg = queue_msg_to_bot.get(block = False)
        except queue.Empty:
            is_ok = False
            return is_ok
        else:
            is_ok = True

        # Validate map_msg
        #
        if not isinstance(map_msg, collections.abc.Mapping):
            raise RuntimeError(
                    'Invalid message recieved: {map_msg}'.format(
                                                    map_msg = repr(map_msg)))

        # ---------------------------------------------------------------------
        async def on_button_press_generic(interaction, *args):
            """
            Generic button press callback.

            """
            map_cmd = dict(type       = 'interaction',
                           id_btn     = interaction.data['custom_id'],
                           id_user    = interaction.user.id,
                           name_user  = interaction.user.name,
                           id_channel = interaction.channel.id)
            try:
                queue_cmd_from_bot.put(map_cmd, block = False)
            except queue.Full:
                log.error('Button input dropped. ' \
                          'queue_cmd_from_bot is full.')

            await interaction.response.defer()


        # =================================================================
        class ButtonView(discord.ui.View):
            """
            A view containing a single button.

            """

            # -------------------------------------------------------------
            def __init__(self,
                         style,
                         label,
                         id_btn,
                         callback,
                         timeout = 360):

                """
                Construct the button view.

                """
                super().__init__(timeout = timeout)
                self.button = discord.ui.Button(style     = style,
                                                label     = label,
                                                custom_id = id_btn)
                self.button.callback = callback
                self.add_item(self.button)

        # If we have a new message to
        # handle, then simply send it
        # to the specified channel.
        #
        type_msg = map_msg.pop('type', 'msg')
        if type_msg == 'dm':
            id_user = map_msg.pop('id_user')
            if id_user not in map_user.keys() or map_user[id_user] is None:
                map_user[id_user] = await bot.fetch_user(id_user)
            if map_user[id_user] is None:
                log.critical(
                    'Unable to access user: {id}. ' \
                    'Please check permissions.'.format(id = str(id_user)))
            destination = map_user[id_user]

        elif type_msg == 'msg':
            id_chan = map_msg.pop('id_channel')
            if id_chan not in map_chan.keys() or map_chan[id_chan] is None:
                map_chan[id_chan] = await bot.fetch_channel(id_chan)
            if map_chan[id_chan] is None:
                log.critical(
                    'Unable to access channel: {id}. ' \
                    'Please check permissions.'.format(id = str(id_chan)))
            destination = map_chan[id_chan]

        else:
            raise RuntimeError(
                    'Did not recognise message type: {type}'.format(
                                                            type = type_msg))

        # Messages are a dict with fields
        # that correspond to the keyword
        # args of the discord channel
        # send function.
        #
        # https://discordpy.readthedocs.io/en/stable/api.html#channels
        #
        # We want to be able to send files
        # without requiring access to the
        # local filesystem, so we add
        # special handling for 'file'
        # fields to support the use of
        # a FileData named tuple, which
        # allows us to encode the file
        # in an in-memory buffer rather
        # than as a file handle.
        #

        if 'file' in map_msg and isinstance(map_msg['file'], FileData):
            file_data       = map_msg['file']
            map_msg['file'] = discord.File(
                                    fp          = io.BytesIO(file_data.buffer),
                                    filename    = file_data.filename,
                                    spoiler     = file_data.spoiler,
                                    description = file_data.description)

        if 'button' in map_msg and isinstance(map_msg['button'], ButtonData):
            button_data     = map_msg.pop('button')
            map_msg['view'] = ButtonView(
                                    style    = discord.ButtonStyle.green,
                                    label    = button_data.label,
                                    id_btn   = button_data.id_btn,
                                    callback = on_button_press_generic)

        await destination.send(**map_msg)

        return is_ok


    # -------------------------------------------------------------------------
    async def _service_cmd_queue(queue_cmd_to_bot, map_cmd):
        """
        Service command configuration from the system to discord.

        """
        try:
            cfg_cmd = queue_cmd_to_bot.get(block = False)
        except queue.Empty:
            is_ok = False
            return is_ok
        else:
            is_ok = True

        # Validate cfg_cmd.
        #
        tup_key_required = ('name', 'description')
        for str_key in tup_key_required:
            if str_key not in cfg_cmd:
                raise ValueError(
                        'Command configuration is '\
                        'Missing key: {key}.'.format(key = str_key))
            if not isinstance(cfg_cmd[str_key], str):
                raise ValueError(
                        'Command configuration {key} should be '\
                        'a string. Got {typ} instead.'.format(
                                key = str_key,
                                typ = type(cfg_cmd[str_key]).__name__))

        # ---------------------------------------------------------------------
        async def on_command_generic(ctx, *args):
            """
            Generic command callback.

            """
            map_cmd = dict(type         = 'command',
                           args         = ctx.args[1:],
                           kwargs       = ctx.kwargs,
                           prefix       = ctx.prefix,
                           name_command = ctx.command.name,
                           id_guild     = ctx.guild.id,
                           name_guild   = ctx.guild.name,
                           id_channel   = ctx.channel.id,
                           name_channel = ctx.channel.name,
                           id_author    = ctx.author.id,
                           name_author  = ctx.author.name,
                           nick_author  = ctx.author.nick)
            try:
                queue_cmd_from_bot.put(map_cmd, block = False)
            except queue.Full:
                log.error('Command input dropped. ' \
                          'queue_cmd_from_bot is full.')

        map_cmd[cfg_cmd['name']] = discord.ext.commands.Command(
                                        on_command_generic,
                                        name = cfg_cmd['name'],
                                        help = cfg_cmd['description'])
        bot.add_command(map_cmd[cfg_cmd['name']])

        return is_ok


    # -------------------------------------------------------------------------
    @bot.event
    async def on_command_error(ctx, error):
        """
        Handle errors in commands.

        """

        # We make some specififc errors visible
        # to the user on the client side.
        #
        if isinstance(error, (discord.ext.commands.CommandNotFound,
                              discord.ext.commands.DisabledCommand,
                              discord.ext.commands.DisabledCommand)):

            await ctx.send(str(error))

        # Anything else, we send a generic error
        # message to the user and raise an
        # exception that is logged on the sever
        # so the developer can address it.
        #
        else:

            str_msg = 'An error has been logged.'
            await ctx.send(str_msg)
            raise error


    # -------------------------------------------------------------------------
    @bot.event
    async def on_message(message):
        """
        Handle messages that are sent to the client.

        This coroutine is invoked
        whenever a message is created
        and sent.

        This coroutine is intended to
        simply forward the content of
        the message to the rest of the
        system via the queue_msg_from_bot
        queue.

        """

        await bot.process_commands(message)

        if message.author.bot:
            return

        if message.content.startswith(BOT_COMMAND_PREFIX):
            return
        
        if isinstance(message.channel, discord.DMChannel):
            msg = dict(msg_type     = 'dm',
                    id_prev      = None,
                    id_msg       = message.id,
                    id_author    = message.author.id,
                    name_author  = message.author.name,
                    id_channel   = message.channel.id,
                    name_channel = None,
                    content      = message.content)
        else:
            msg = dict(msg_type     = 'message',
                    id_prev      = None,
                    id_msg       = message.id,
                    id_author    = message.author.id,
                    name_author  = message.author.name,
                    id_channel   = message.channel.id,
                    name_channel = message.channel.name,
                    content      = message.content)

        try:
            queue_msg_from_bot.put(msg, block = False)
        except queue.Full:
            log.error('Message dropped. queue_msg_from_bot is full.')

    # -------------------------------------------------------------------------
    @bot.event
    async def on_message_edit(msg_before, msg_after):
        """
        Handle message-edits that are sent to the client.

        This coroutine is invoked
        whenever a message receives
        an update event. If the
        message is not found in the
        internal message cache, then
        these events will not be
        called.

        Messages might not be in
        cache if the message is
        too old or the client is
        participating in high
        traffic guilds.

        This coroutine is intended to
        simply forward the content of
        the message to the rest of the
        system via the queue_msg_from_bot
        queue.

        """

        if msg_after.author.bot:
            return

        if msg_after.content.startswith(BOT_COMMAND_PREFIX):
            return
        
        if isinstance(msg_before.channel, discord.DMChannel):
            msg = dict(msg_type     = 'dm',
                       id_prev      = msg_before.id,
                       id_msg       = msg_after.id,
                       id_author    = msg_after.author.id,
                       name_author  = msg_after.author.name,
                       id_channel   = msg_after.channel.id,
                       name_channel = None,
                       content      = msg_after.content)
        else:
            msg = dict(msg_type     = 'message',
                       id_prev      = msg_before.id,
                       id_msg       = msg_after.id,
                       id_author    = msg_after.author.id,
                       name_author  = msg_after.author.name,
                       nick_author  = msg_after.author.nick,
                       id_channel   = msg_after.channel.id,
                       name_channel = msg_after.channel.name,
                       content      = msg_after.content)

        try:
            queue_msg_from_bot.put(msg, block = False)
        except queue.Full:
            log.error('Message dropped. queue_msg_from_bot is full.')


    # -------------------------------------------------------------------------
    @bot.command(name = "clear_all_messages")
    async def clear_all_messages(ctx):
        """
        Clear all messages in the channel.

        This is rate limited to about 2 commands
        per second. (Discord server side rate
        limit is 5 requests per second per API
        token).

        This requires the "Manage Messages" bot
        permission.

        """

        async for message in ctx.channel.history(limit = 300):
            await message.delete()
            await asyncio.sleep(0.5)  # add delay to prevent hitting rate limits


    # -------------------------------------------------------------------------
    # Run the client.
    #
    # bot.run(token       = cfg_bot['str_token'],
    #         reconnect   = False,
    #         log_level   = logging.INFO,
    #         log_handler = loghandler)
    bot.run(token       = cfg_bot['str_token'],
            reconnect   = False,
            log_level   = logging.INFO)
