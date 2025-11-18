import asyncio
import datetime
import os
import re as regex
import tempfile
import time
from urllib.parse import urlparse

import diskcache
import socks
from aiohttp import web
from asyncstdlib.functools import lru_cache as async_lru_cache
from telethon import TelegramClient, errors, events
from telethon import utils as telethon_utils
from telethon.extensions import html, markdown
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (
  DeleteChannelRequest,
  DeleteHistoryRequest,
  JoinChannelRequest,
  LeaveChannelRequest,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import PeerChannel

from config import _current_path as current_path
from config import config
from logger import logger
from utils import db_model as utils
from utils.common import (
  banner,
  build_sublist_msg,
  get_event_chat_username,
  get_event_chat_username_list,
  is_allow_access,
  is_msg_block,
)

# Configure proxy to access tg server
proxy = None
if all(config['proxy'].values()): # All are not None
  logger.info(f'proxy info:{config["proxy"]}')
  proxy = (getattr(socks,config['proxy']['type']), config['proxy']['address'], config['proxy']['port'])
# proxy = (socks.SOCKS5, '127.0.0.1', 1088)

account = config['account']

if not account['reader_session_id']:
  raise ValueError('reader_session_id is required in config.yml')

account['bot_name'] = account.get('bot_name') or account['bot_username']
tmp_path = f'{current_path}/.tmp/'
cache = diskcache.Cache(tmp_path)# Set cache file directory, current tmp folder. Used to cache step-by-step command operations to avoid bot unable to find current input operation progress

# Initialize clients but don't start them yet (will start in main())
client = TelegramClient(StringSession(account['reader_session_id']), account['api_id'], account['api_hash'], proxy = proxy)
bot = TelegramClient(f'{tmp_path}/.{account["bot_name"]}', account['api_id'], account['api_hash'],proxy = proxy)

# Health check endpoint for Kamal deployment
async def health_check(request):
    """Health check endpoint for container orchestration"""
    return web.Response(text='OK', status=200)

async def run_health_server():
    """Run health check HTTP server on port 80"""
    app = web.Application()
    app.router.add_get('/up', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 80)
    await site.start()
    logger.info('Health check server started on port 80')
    # Keep the server running
    while True:
        await asyncio.sleep(3600)

def js_to_py_re(rx):
  '''
  Parse JS regex string for use in Python
  Only supports i and g flags
  '''
  query, params = rx[1:].rsplit('/', 1)
  if 'g' in params:
      obj = regex.findall
  else:
      obj = regex.search

  # May need to make flags= smarter, but just an example...
  return lambda L: obj(query, L, flags=regex.I if 'i' in params else 0)

def is_regex_str(string):
  """
    Strict regex validation
    :param rule: Input rule string
    :return: True if valid regex syntax
  """
  # return regex.search(r'^/.*/[a-zA-Z]*?$',string)
  try:
      query, params = string[1:].rsplit('/', 1)
      if query:
        regex.compile(query)  # Compile regex
        return True
  except:
      return False

  return False

def is_regex_str_fuzzy(rule):
  return is_regex_str(rule)
  # match = regex.fullmatch(r"^/(.+)/([a-zA-Z]*)$", rule)
  # return bool(match)

@async_lru_cache(maxsize=None)
async def client_get_entity(entity,_):
  '''
  Read channel information
  Memory cache alternative to client.get_entity

  Avoid frequent request errors from get_entity
  A wait of 19964 seconds is required (caused by ResolveUsernameRequest)

  Args:
      entity (_type_): Same as get_entity() parameter
      _ (_type_): LRU cache marker value

  Example:
    Cache 1 day
    await client_get_entity(real_id, time.time() // 86400 )

    Cache 10 seconds
    await client_get_entity(real_id, time.time() // 10 )

  Returns:
      Entity:
  '''
  return await client.get_entity(entity)



async def cache_set(*args):
  '''
  Cache write, async mode

  wiki：https://github.com/grantjenks/python-diskcache/commit/dfad0aa27362354901d90457e465b8b246570c3e

  Returns:
      _type_: _description_
  '''
  loop = asyncio.get_running_loop()
  future = loop.run_in_executor(None, cache.set, *args)
  result = await future
  return result

async def cache_get(*args):
  loop = asyncio.get_running_loop()
  future = loop.run_in_executor(None, cache.get, *args)
  result = await future
  return result

async def resolve_invit_hash(invit_hash,expired_secends = 60 * 5):
  '''
  Parse invite link  https://t.me/+G-w4Ovfzp9U4YTFl
  Default cache 5min

  Args:
      invite_hash (str): e.g. G-w4Ovfzp9U4YTFl
      expired_secends (int): None: not cache , 60:  1min

  Returns:
      Tuple | None: (marked_id,chat_title)
  '''
  if not invit_hash: return None
  marked_id = ''
  chat_title = ''

  cache_key = f'01211resolve_invit_hash{invit_hash}'
  find = await cache_get(cache_key)
  if find:
    logger.info(f'resolve_invit_hash HIT CACHE: {invit_hash}')
    return find

  logger.info(f'resolve_invit_hash MISS: {invit_hash}')
  chatinvite = await client(CheckChatInviteRequest(invit_hash))
  if chatinvite and hasattr(chatinvite,'chat'):# Already joined
    # chatinvite.chat.id # 1695903641
    # chatinvite.chat.title # 'test'

    marked_id  = telethon_utils.get_peer_id(PeerChannel(chatinvite.chat.id)) # Convert to marked_id
    chat_title = chatinvite.chat.title
    channel_entity = chatinvite.chat
    rel = (marked_id,chat_title,channel_entity)
    await cache_set(cache_key,rel,expired_secends)
    # cache.set(cache_key,rel,expired_secends)
    return rel
  return None

async def copy_and_send_message(event, receiver):
  '''
  Copy message content (text, images, files) and send as a new bot message
  This is used as a fallback when forwarding fails

  Args:
      event: The event containing the message to copy
      receiver: The chat_id of the receiver to send to

  Returns:
      bool: True if the message was sent, False otherwise
  '''
  try:
    message = event.message
    logger.debug(f'copy_and_send_message called: receiver={receiver}, message.id={message.id}')

    # Get message text
    text = message.text or message.raw_text or ''

    # Check if message has media (photo, document, video, audio, etc.)
    has_media = (message.photo is not None or
                message.video is not None or
                message.audio is not None or
                message.document is not None or
                message.sticker is not None or
                message.voice is not None or
                message.video_note is not None)

    if has_media:
      # Download and send media with caption
      try:
        # First, try to get the media from the message
        # Download media using client (which has access to the channel)
        # Download to a temporary file path
        with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
          tmp_path = tmp_file.name

        try:
          # Download the media file to temporary path
          downloaded_path = await client.download_media(message, file=tmp_path)
          if downloaded_path and os.path.exists(downloaded_path):
            # Send the downloaded file with caption
            await bot.send_file(receiver, downloaded_path, caption=text if text else None)
            logger.info(f'Copied and sent media message {message.id} to receiver {receiver}')
            # Clean up temporary file
            try:
              os.unlink(downloaded_path)
            except Exception:
              pass
            return True
          else:
            logger.warning(f'Failed to download media for message {message.id}')
        finally:
          # Clean up temp file if it still exists
          try:
            if os.path.exists(tmp_path):
              os.unlink(tmp_path)
          except Exception:
            pass
      except Exception as media_err:
        logger.error(f'Error copying media for message {message.id}: {media_err}', exc_info=True)
        # Fall through to send text only

    # If no media or media download failed, send text only
    if text:
      await bot.send_message(receiver, text)
      logger.info(f'Copied and sent text message {message.id} to receiver {receiver}')
      return True
    else:
      logger.warning(f'Message {message.id} has no text or media to copy')
      return False

  except Exception as e:
    logger.error(f'Error copying and sending message to {receiver}: {type(e).__name__}: {e}', exc_info=True)
    return False

async def forward_matched_message(event, receiver, from_peer=None):
  '''
  Forward the matched message that contains the pattern

  Args:
      event: The current event that triggered the pattern match
      receiver: The chat_id of the receiver to forward to
      from_peer: The chat entity or ID to forward from (optional, defaults to event.chat_id or event.chat)

  Returns:
      bool: True if the message was forwarded, False otherwise
  '''
  try:
    message = event.message
    is_forwarded = message.fwd_from is not None
    logger.debug(f'forward_matched_message called: receiver={receiver}, from_peer={from_peer}, event.chat_id={event.chat_id}, event.chat={event.chat}, is_forwarded={is_forwarded}')

    # For forwarded messages, we still forward from the current chat
    # Telegram will automatically preserve the forward chain
    # Determine the from_peer: use provided parameter, or prefer event.chat entity, fallback to event.chat_id
    if from_peer is None:
      logger.debug('from_peer is None, determining from event')
      # Prefer using the entity object directly as it's more reliable
      if event.chat is not None:
        from_peer = event.chat
        logger.debug(f'Using event.chat entity: {from_peer}')
      elif event.chat_id is not None:
        from_peer = event.chat_id
        logger.debug(f'Using event.chat_id: {from_peer}')
      else:
        logger.error('Cannot forward message: event.chat_id is None and event.chat is None')
        return False

    # Ensure from_peer is not None before forwarding
    if from_peer is None:
      logger.error('Cannot forward message: from_peer is None after determination')
      return False

    # Try to resolve the entity if it's a raw ID
    # Use client.get_input_entity() instead of bot.get_input_entity() because
    # the client (reader) has access to channels that the bot might not have access to
    try:
      # If from_peer is already an entity object, use it directly
      # Otherwise, try to resolve it using client.get_input_entity() (reader client has channel access)
      if isinstance(from_peer, (int, str)):
        logger.debug(f'Resolving entity for from_peer: {from_peer} (type: {type(from_peer)})')
        resolved_entity = await client.get_input_entity(from_peer)
        from_peer = resolved_entity
        logger.debug(f'Resolved entity: {resolved_entity}')
      # If it's already an entity object, use it as-is
    except ValueError as ve:
      # Entity cannot be resolved (e.g., channel/user ID that client doesn't have access to)
      logger.warning(f'Cannot resolve entity for from_peer {from_peer}: {ve}. Attempting to copy message content instead.')
      # Fallback: copy message content and send as new bot message
      return await copy_and_send_message(event, receiver)
    except Exception as resolve_err:
      logger.warning(f'Error resolving entity for from_peer {from_peer}: {resolve_err}. Attempting forward with original from_peer.')
      # Continue with original from_peer, let forward_messages handle it

    logger.debug(f'Forwarding message {event.message.id} to receiver {receiver} from {from_peer} (type: {type(from_peer)}), is_forwarded: {is_forwarded}')
    # Forward the matched message
    # For forwarded messages, Telegram will preserve the forward information automatically
    # Note: bot.forward_messages() will try to resolve the entity again internally
    # If it fails, we catch the ValueError and skip forwarding gracefully
    try:
      await bot.forward_messages(receiver, [event.message.id], from_peer)
      logger.info(f'Forwarded matched message {event.message.id} to receiver {receiver} from {from_peer}')
      return True
    except ValueError as ve:
      # Bot cannot resolve the entity (e.g., bot doesn't have access to the channel)
      # This can happen even if client resolved it, because bot and client have different access
      logger.warning(f'Bot cannot forward from entity {from_peer}: {ve}. Attempting to copy message content instead.')
      # Fallback: copy message content and send as new bot message
      return await copy_and_send_message(event, receiver)
  except Exception as e:
    logger.error(f'Error forwarding matched message to {receiver}: {type(e).__name__}: {e}', exc_info=True)
    return False

# Client-related operations, purpose: read messages
@client.on(events.MessageEdited)
@client.on(events.NewMessage())
async def on_greeting(event):
    '''Greets someone'''
    # telethon.events.newmessage.NewMessage.Event
    # telethon.events.messageedited.MessageEdited.Event
    logger.debug(f'on_greeting called: event.chat={event.chat}, event.chat_id={event.chat_id}, event.message.id={event.message.id if event.message else "N/A"}')
    if not event.chat: # Private group appears as None
      channel_entity = await client_get_entity(event.chat_id,None)
      if channel_entity:
        event_chat = channel_entity
        event_chat.username = ''
      else:
        logger.error(f'event_chat empty. event: { event }')
        raise events.StopPropagation
    else:
      event_chat = event.chat

    if not hasattr(event_chat,'username'):
      logger.error(f'event_chat not found username:{event_chat}')
      raise events.StopPropagation

    if event_chat.username == account['bot_name']: # Don't listen to current bot messages
      logger.debug(f'Don\'t listen to current bot messages, event_chat.username: { event_chat.username }')
      raise events.StopPropagation

    # Whether to reject messages from other bots sent in groups
    if 'block_bot_msg' in config and config['block_bot_msg']:
      if hasattr(event.message.sender,'bot') and event.message.sender.bot :
        logger.debug(f'Don\'t listen to all bot messages, event_chat.username: { event_chat.username }')
        raise events.StopPropagation

    # if not event.is_group:# channel type
    if True:# All message types, support groups
      message = event.message

      text = message.text
      if message.file and message.file.name:
        # text += ' file:{}'.format(message.file.name)# Append filename
        text += ' {}'.format(message.file.name)# Append filename

      # Print message
      _title = ''
      if not hasattr(event_chat,'title'):
        logger.warning(f'event_chat not found title:{event_chat}')
      else:
        _title = f'event.chat.title:{event_chat.title},'
      logger.debug(f'event.chat.username: {get_event_chat_username(event_chat)},event.chat.id:{event_chat.id},{_title} event.message.id:{event.message.id},text:{text}')

      # 1. Method (failed): forward messages
      # chat = 'keyword_alert_bot' # Can forward but cannot forward to specific users. Can only forward to bot of currently allowed account
      # from_chat = 'tianfutong'
      # chat = 349506543# Unable to forward directly using chat_id, no response
      # chat = 1354871670
      # await message.forward_to('keyword_alert_bot')
      # await client.forward_messages(chat, message)
      # await bot.forward_messages(chat, message)
      # await client.forward_messages(chat, message.id, from_chat)

      # 2. Method: send new message directly, not forward. But can use URL preview to achieve effect

      # Find all subscriptions for current channel
      logger.debug(f'Getting username list for event_chat: {event_chat}, type: {type(event_chat)}')
      event_chat_username_list = get_event_chat_username_list(event_chat)
      event_chat_username = get_event_chat_username(event_chat)
      logger.debug(f'event_chat_username_list: {event_chat_username_list}, type: {type(event_chat_username_list)}, event_chat_username: {event_chat_username}')
      # Safety check: ensure event_chat_username_list is a list
      if event_chat_username_list is None:
        logger.warning(f'event_chat_username_list is None, converting to empty list. event_chat: {event_chat}')
        event_chat_username_list = []
      if not isinstance(event_chat_username_list, (list, tuple)):
        logger.warning(f'event_chat_username_list is not a list/tuple: {type(event_chat_username_list)}, converting to list')
        event_chat_username_list = list(event_chat_username_list) if event_chat_username_list else []
      placeholders = ','.join('?' for _ in event_chat_username_list)# Placeholder fill
      logger.debug(f'Generated placeholders: {placeholders}')

      condition_strs = ['l.chat_id = ?']
      if event_chat_username_list:
        condition_strs.append(f' l.channel_name in ({placeholders}) ')

      sql = f"""
      select u.chat_id,l.keywords,l.id,l.chat_id
from user_subscribe_list as l
INNER JOIN user as u on u.id = l.user_id
where ({' OR '.join(condition_strs)}) and l.status = 0  order by l.create_time  desc
      """

      # bind = [str(event.chat_id)]
      # Use event_chat.id if available, otherwise fall back to event.chat_id
      chat_id_for_bind = event_chat.id if (hasattr(event_chat, 'id') and event_chat.id is not None) else event.chat_id
      if chat_id_for_bind is None:
        logger.error(f'Cannot determine chat_id for query. event.chat_id: {event.chat_id}, event_chat: {event_chat}')
        raise events.StopPropagation
      bind = [str(telethon_utils.get_peer_id(PeerChannel(chat_id_for_bind)))] # Ensure query and storage id units are unified marked_id
      if event_chat_username_list:
        bind += event_chat_username_list

      logger.debug(f'Executing SQL query with bind: {bind}, sql: {sql}')
      find = utils.db.connect.execute_sql(sql,tuple(bind)).fetchall()
      logger.debug(f'SQL query result type: {type(find)}, length: {len(find) if find else 0}')
      # Safety check: ensure find is iterable (should be a list, but handle None case)
      if find is None:
        logger.warning('SQL query returned None, converting to empty list')
        find = []
      if find:
        logger.info(f'channel: {event_chat_username_list}; all chat_id & keywords:{find}') # Print current channel, subscribed users and keywords

        logger.debug(f'Processing {len(find)} subscription matches. event.chat_id: {event.chat_id}, event_chat.id: {event_chat.id if hasattr(event_chat, "id") else "N/A"}')
        for receiver,keywords,l_id,l_chat_id in find:
          try:
            logger.debug(f'Processing subscription: receiver={receiver}, keywords={keywords}, l_id={l_id}, l_chat_id={l_chat_id}')
            # Message send deduplication rule
            MSG_UNIQUE_RULE_MAP = {
              'SUBSCRIBE_ID': f'{receiver}_{l_id}',
              'MESSAGE_ID': f'{receiver}_{message.id}',
            }
            if 'msg_unique_rule' not in config:
              config['msg_unique_rule'] = 'SUBSCRIBE_ID'
            assert config['msg_unique_rule'] in MSG_UNIQUE_RULE_MAP,'config "msg_unique_rule" error!!!'
            CACHE_KEY_UNIQUE_SEND = MSG_UNIQUE_RULE_MAP[config['msg_unique_rule']]
            logger.debug(f'msg_unique_rule:{config["msg_unique_rule"]} --> {CACHE_KEY_UNIQUE_SEND}')

            # Prefer returning previewable URL
            channel_url = f'https://t.me/{event_chat_username}/' if event_chat_username else get_channel_url(event_chat_username,event.chat_id)

            channel_msg_url= f'{channel_url}{message.id}'
            send_cache_key = f'_LAST_{l_id}_{message.id}_send'
            if isinstance(event,events.MessageEdited.Event):# Edit event
              # Don't alert for edits 2 seconds after creation within 24 hours
              if cache.get(send_cache_key) and (event.message.edit_date - event.message.date) > datetime.timedelta(seconds=2):
                logger.error(f'{channel_msg_url} repeat send. deny!')
                continue
            if not l_chat_id:# Channel id not recorded
              logger.info(f'update user_subscribe_list.chat_id:{event.chat_id}  where id = {l_id} ')
              re_update = utils.db.user_subscribe_list.update(chat_id = str(event.chat_id) ).where(utils.User_subscribe_list.id == l_id)
              re_update.execute()

            chat_title = event_chat_username or (event.chat.title if event.chat and hasattr(event.chat, 'title') else '')
            if is_regex_str_fuzzy(keywords):# Input is regex string
              logger.debug(f'Processing regex match for keywords: {keywords}, receiver: {receiver}, l_id: {l_id}')
              try:
                regex_match = js_to_py_re(keywords)(text)# Perform regex match, only supports i and g flags
                logger.debug(f'Regex match result type: {type(regex_match)}, value: {regex_match}')
              except Exception as regex_err:
                logger.error(f'Error executing regex {keywords}: {regex_err}')
                regex_match = None

              if regex_match is None:
                logger.debug(f'regex_match is None for keywords: {keywords}')
                regex_match_str = []
              elif isinstance(regex_match,regex.Match):#search() result
                regex_match = [regex_match.group()]
                regex_match_str = []# Display content
                for _ in regex_match:
                  item = ''.join(_) if isinstance(_,tuple) else _
                  if item:
                    regex_match_str.append(item) # Merge and remove spaces
                regex_match_str = list(set(regex_match_str))# Remove duplicates
              else:
                # regex_match should be a list from findall()
                regex_match_str = []# Display content
                if not isinstance(regex_match, (list, tuple)):
                  logger.warning(f'Unexpected regex_match type: {type(regex_match)}, converting to list')
                  regex_match = [regex_match] if regex_match else []
                for _ in regex_match:
                  item = ''.join(_) if isinstance(_,tuple) else _
                  if item:
                    regex_match_str.append(item) # Merge and remove spaces
                regex_match_str = list(set(regex_match_str))# Remove duplicates
              if regex_match_str:# Default findall() result
                # # {chat_title} \n\n
                channel_title = f"\n\nCHANNEL: {chat_title}" if not event_chat_username else ""

                message_str = f'[#FOUND]({channel_msg_url}) **{regex_match_str}**{channel_title}'
                if cache.add(CACHE_KEY_UNIQUE_SEND,1,expire=5):
                  logger.info(f'REGEX: receiver chat_id:{receiver}, l_id:{l_id}, message_str:{message_str}')
                  if isinstance(event,events.NewMessage.Event):# New message event
                    cache.set(send_cache_key,1,expire=86400) # Send marker cached for one day

                  # Blacklist check
                  if is_msg_block(receiver=receiver,msg=message.text,channel_name=event_chat_username,channel_id=event.chat_id):
                    continue

                  # Forward the matched message - prefer event_chat entity directly, fallback to ID
                  # Using the entity object directly is more reliable than just the ID
                  if event_chat is not None:
                    from_peer = event_chat  # Prefer entity object directly
                  elif event.chat_id is not None:
                    from_peer = event.chat_id
                  else:
                    logger.warning(f'Cannot determine from_peer for forwarding: event_chat={event_chat}, event.chat_id={event.chat_id}')
                    from_peer = None
                  await forward_matched_message(event, receiver, from_peer=from_peer)

                  await bot.send_message(receiver, message_str,link_preview = True,parse_mode = 'markdown')
                else:
                  # Message already sent
                  logger.debug(f'REGEX send repeat. rule_name:{config["msg_unique_rule"]}  {CACHE_KEY_UNIQUE_SEND}:{channel_msg_url}')
                  continue

              else:
                logger.debug(f'regex_match empty. regex:{keywords} ,message: {channel_msg_url}')
            else:# Normal mode
              if keywords in text:
                # # {chat_title} \n\n
                channel_title = f"\n\nCHANNEL: {chat_title}" if not event_chat_username else ""
                message_str = f'[#FOUND]({channel_msg_url}) **{keywords}**{channel_title}'
                if cache.add(CACHE_KEY_UNIQUE_SEND,1,expire=5):
                  logger.info(f'TEXT: receiver chat_id:{receiver}, l_id:{l_id}, message_str:{message_str}')
                  if isinstance(event,events.NewMessage.Event):# New message event
                    cache.set(send_cache_key,1,expire=86400) # Send marker cached for one day

                  # Blacklist check
                  if is_msg_block(receiver=receiver,msg=message.text,channel_name=event_chat_username,channel_id=event.chat_id):
                    continue

                  # Forward the matched message - prefer event_chat entity directly, fallback to ID
                  # Using the entity object directly is more reliable than just the ID
                  if event_chat is not None:
                    from_peer = event_chat  # Prefer entity object directly
                  elif event.chat_id is not None:
                    from_peer = event.chat_id
                  else:
                    logger.warning(f'Cannot determine from_peer for forwarding: event_chat={event_chat}, event.chat_id={event.chat_id}')
                    from_peer = None
                  await forward_matched_message(event, receiver, from_peer=from_peer)

                  await bot.send_message(receiver, message_str,link_preview = True,parse_mode = 'markdown')
                else:
                  # Message already sent
                  logger.debug(f'TEXT send repeat. rule_name:{config["msg_unique_rule"]}  {CACHE_KEY_UNIQUE_SEND}:{channel_msg_url}')
                  continue
          except errors.rpcerrorlist.UserIsBlockedError  as _e:
            # User is blocked (caused by SendMessageRequest)  User manually stopped bot
            logger.error(f'{_e}')
            pass # Close all subscriptions
          except ValueError  as _e:
            # User never used bot
            logger.error(f'{_e}')
            # Delete user subscriptions and id
            isdel = utils.db.user.delete().where(utils.User.chat_id == receiver).execute()
            user_id = utils.db.user.get_or_none(chat_id=receiver)
            if user_id:
              isdel2 = utils.db.user_subscribe_list.delete().where(utils.User_subscribe_list.user_id == user_id.id).execute()
          except AssertionError as _e:
            raise _e
          except Exception as _e:
            logger.error(f'Error processing subscription (receiver={receiver}, l_id={l_id}, keywords={keywords}): {type(_e).__name__}: {_e}', exc_info=True)
      else:
        logger.debug(f'sql find empty. event.chat.username:{event_chat_username}, find:{find}, sql:{sql}')

        if 'auto_leave_channel' in config and config['auto_leave_channel']:
          if event_chat_username:# Public channel/group
            logger.info(f'Leave  Channel/group: {event_chat_username}')
            await leave_channel(event_chat_username)


# Bot-related operations
def parse_url(url):
  """
  Parse URL information
  Based on urllib.parse operation to avoid it setting semicolon as parameter separator causing params issues
  Args:
      url ([type]): [string]

  Returns:
      [dict]: [Field area names as I understand them]  <scheme>://<host>/<uri>?<query>#<fragment>
  """
  if regex.search(r'^t\.me/',url):
    url = f'http://{url}'

  res = urlparse(url) # <scheme>://<netloc>/<path>;<params>?<query>#<fragment>
  result = {}
  result['scheme'],result['host'],result['uri'],result['_params'],result['query'],result['fragment'] = list(res)
  if result['_params'] or ';?' in url:
    result['uri'] += ';'+result['_params']
    del result['_params']
  return result

def get_channel_url(event_chat_username,event_chat__id):
  """
  Get channel/group URL
  Prefer returning URL with chat_id

  https://docs.telethon.dev/en/latest/concepts/chats-vs-channels.html#converting-ids

  Args:
      event_chat_username (str): Channel name address e.g. tianfutong
      event_chat__id (str): Channel unofficial id. e.g. -1001630956637
  """
  # event.is_private cannot determine
  # Determine private channel
  # is_private = True if not event_chat_username else False
  host = 'https://t.me/'
  url = ''
  if event_chat__id:
    real_id, peer_type = telethon_utils.resolve_id(int(event_chat__id)) # Convert to official real id
    url = f'{host}c/{real_id}/'
  elif event_chat_username:
    url = f'{host}{event_chat_username}/'
  return url


def parse_full_command(command, keywords, channels):
  """
  Process multi-field command parameters, concatenate and return
    Args:
        command ([type]): [Command such as subscribe  unsubscribe]
        keywords ([type]): [description]
        channels ([type]): [description]

    Returns:
        [type]: [description]
  """
  keywords_list = keywords.split(',')
  if is_regex_str(keywords):# Regex string
    keywords_list = [keywords]

  channels_list = channels.split(',')
  res = {}
  for keyword in keywords_list:
    keyword = keyword.strip()
    for channel in channels_list:
      channel = channel.strip()
      uri = parse_url(channel)['uri']
      channel = uri.strip('/')
      channel = regex.sub('^joinchat/(.+)',r'+\1',channel)
      find_channel = regex.search(r'^c/(\d+)|^(\+.+)',channel)
      if find_channel:
        for i in find_channel.groups():
          if i:
            channel = i
            break
      res[f'{channel}{keyword}'] = (keyword,channel)# Deduplicate
  return list(res.values())

async def join_channel_insert_subscribe(user_id,keyword_channel_list):
  """
  Join channel and write to subscription data table

  Supports passing channel id

  Raises:
      events.StopPropagation: [description]
  """
  res = []
  # Join channel
  for k,c in keyword_channel_list:
    username = ''
    chat_id = ''
    try:
      is_chat_invite_link = False
      if c.lstrip('-').isdigit():# Integer
        real_id, peer_type = telethon_utils.resolve_id(int(c))
        channel_entity = None
        # Don't request channel_entity
        # channel_entity = await client_get_entity(real_id, time.time() // 86400 )
        chat_id = telethon_utils.get_peer_id(PeerChannel(real_id)) # Convert to marked_id
      else:# Pass normal name
        if regex.search(r'^\+',c):# Invite link
          is_chat_invite_link = True
          c = c.lstrip('+')
          channel_entity = None
          chat_id = ''
          chatinvite =  await resolve_invit_hash(c)
          if chatinvite:
            chat_id,chat_title,channel_entity = chatinvite
        else:
          channel_entity = await client_get_entity(c, time.time() // 86400)
          chat_id = telethon_utils.get_peer_id(PeerChannel(channel_entity.id)) # Convert to marked_id

      if channel_entity:
        username = get_event_chat_username(channel_entity) or ''

      if channel_entity and not channel_entity.left: # Already joined this channel
        logger.warning(f'user_id:{user_id} triggered check, already joined this private channel:{chat_id}  invite_hash:{c}')
        res.append((k,username,chat_id))
      else:
        if is_chat_invite_link:
          # Join private channel via invite link
          logger.info(f'user_id:{user_id} joining private channel via invite link {c}')
          await client(ImportChatInviteRequest(c))
          chatinvite =  await resolve_invit_hash(c)
          if chatinvite:
            chat_id,chat_title,channel_entity = chatinvite
            res.append((k,username,chat_id))
        else:
          await client(JoinChannelRequest(channel_entity or chat_id))
          res.append((k,username,chat_id))

    except errors.InviteHashExpiredError as _e:
      logger.error(f'{c} InviteHashExpiredError ERROR:{_e}')
      return f'Unable to use this channel invite link: {c}\nLink has expired.'
    except errors.UserAlreadyParticipantError as _e:# Duplicate join private channel
      logger.warning(f'{c} UserAlreadyParticipantError ERROR:{_e}')
      return 'Unable to use this channel invite link: UserAlreadyParticipantError'
    except Exception as _e: # Channel doesn't exist
      logger.error(f'{c} JoinChannelRequest ERROR:{_e}')

      # Query if local record exists
      channel_name_or_chat_id = regex.sub(r'^(?:http[s]?://)?t.me/(?:c/)?','',c) # Clean extra information
      find = utils.db.connect.execute_sql('select 1 from user_subscribe_list where status = 0 and (channel_name = ? or chat_id = ?)' ,(channel_name_or_chat_id,channel_name_or_chat_id)).fetchall()
      logger.warning(f'{c} JoinChannelRequest fail. cache join. cache find count: {len(find)}')
      if find:
        if len(find) > 1: # More than 1 record exists, return join success directly
          if channel_name_or_chat_id.lstrip('-').isdigit():# Integer
            res.append((k,'',channel_name_or_chat_id))
          else:
            res.append((k,channel_name_or_chat_id,''))
        else:
          return 'Unable to use this channel: {}\n\nChannel error, unable to use: {}'.format(c,_e)
      else:
        return 'Unable to use this channel: {}\n\nChannel error, unable to use: {}'.format(c,_e)

  # Write to data table
  result = []
  for keyword,channel_name,_chat_id in res:
    if not channel_name: channel_name = ''

    find = utils.db.user_subscribe_list.get_or_none(**{
        'user_id':user_id,
        'keywords':keyword,
        'channel_name':channel_name,
        'chat_id':_chat_id,
      })

    if find:
      re_update = utils.db.user_subscribe_list.update(status = 0 ).where(utils.User_subscribe_list.id == find.id)# Update status
      re_update = re_update.execute()# Update success returns 1, regardless of whether repeated execution
      if re_update:
        result.append((find.id,keyword,channel_name,_chat_id))
    else:
      insert_res = utils.db.user_subscribe_list.create(**{
        'user_id':user_id,
        'keywords':keyword,
        'channel_name':channel_name.replace('@',''),
        'create_time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'chat_id':_chat_id
      })
      if insert_res:
        result.append((insert_res.id,keyword,channel_name,_chat_id))
  return result

async def leave_channel(channel_name):
  '''
  Leave unused channel/group

  Args:
      channel_name ([type]): [description]
  '''
  try:
      await client(LeaveChannelRequest(channel_name))
      await client(DeleteChannelRequest(channel_name))
      await client(DeleteHistoryRequest(channel_name))
      logger.info(f'Leave {channel_name}')
  except Exception as _e: # Channel doesn't exist
      return f'Unable to leave this channel: {channel_name}, {_e}'


def update_subscribe(user_id,keyword_channel_list):
  """
  Update subscription data table (unsubscribe operation)
  """
  # Modify data table
  result = []
  for keyword,channel_name in keyword_channel_list:
    find = utils.db.user_subscribe_list.get_or_none(**{
      'user_id':user_id,
      'keywords':keyword,
      'channel_name':channel_name,
    })
    if find:
      re_update = utils.db.user_subscribe_list.update(status = 1 ).where(utils.User_subscribe_list.id == find)# Update status
      re_update = re_update.execute()# Update success returns 1, regardless of whether repeated execution
      if re_update:
        result.append((keyword,channel_name))
    else:
      result.append((keyword,channel_name))
  return result

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
  """Send a message when the command /start is issued."""
  # insert chat_id
  chat_id = event.message.chat.id

  if chat_id:
    await event.respond(f'Your Telegram Chat ID is: `{chat_id}`')

  # Access authorization check
  if not is_allow_access(chat_id):
    await event.respond('Opps! I\'m a private bot. Sorry, this is a private bot')
    raise events.StopPropagation

  find = utils.db.user.get_or_none(chat_id=chat_id)
  if not find:
    insert_res = utils.db.user.create(**{
      'chat_id':chat_id,
      'create_time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
  else: # chat_id exists
    insert_res = True

  if insert_res:
    await event.respond('Hi! Please input /help , access usage.')
  else:
    await event.respond('Opps! Please try again /start ')

  raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/subscribe'))
async def subscribe(event):
  """Send a message when the command /subscribe is issued."""
  # insert chat_id
  chat_id = event.message.chat.id
  if not is_allow_access(chat_id):
    await event.respond('Opps! I\'m a private bot. Sorry, this is a private bot')
    raise events.StopPropagation

  find = utils.db.user.get_or_none(chat_id=chat_id)
  user_id = find
  if not find:# User information doesn't exist
    await event.respond('Failed. Please input /start')
    raise events.StopPropagation

  text = event.message.text
  text = text.replace('，',',')# Replace Chinese comma
  text = regex.sub(r'\s*,\s*',',',text) # Ensure no spaces between English commas, e.g. "https://t.me/xiaobaiup, https://t.me/com9ji"
  splitd = [i for i in regex.split(r'\s+',text) if i]# Remove empty elements
  if len(splitd) <= 1:
    msg = "Input the keyword that needs to subscribe, support JS regular syntax:\n`/[\s\S]*/ig`\n\nInput the keyword that needs to subscribe, support JS regular syntax:\n`/[\s\S]*/ig`"
    _text, entities = markdown.parse(msg)
    await event.respond(_text,formatting_entities=entities)
    cache.set('status_{}'.format(chat_id),{'current_status':'/subscribe keywords','record_value':text},expire=5*60)# Set to expire after 5m
  elif len(splitd)  == 3:
    command, keywords, channels = splitd
    result = await join_channel_insert_subscribe(user_id,parse_full_command(command, keywords, channels))
    if isinstance(result,str):
        logger.error('join_channel_insert_subscribe error: '+result)
        await event.respond(result,parse_mode = None) # Show error message
    else:
      msg = ''
      for subscribeid,key,channel,_chat_id in result:
        if _chat_id:
          _chat_id, peer_type = telethon_utils.resolve_id(int(_chat_id))

        if not channel:
          channel = f'<a href="t.me/c/{_chat_id}/-1">{_chat_id}</a>'
        msg += build_sublist_msg(subscribeid,'Keywords',key,channel)

      if msg:
        msg = 'success subscribe:\n\n'+msg
        text, entities = html.parse(msg)# 解析超大文本 分批次发送 避免输出报错
        for text, entities in telethon_utils.split_text(text, entities):
          await event.respond(text,formatting_entities=entities)
        #await event.respond('success subscribe:\n'+msg,parse_mode = None)
  raise events.StopPropagation


@bot.on(events.NewMessage(pattern='/unsubscribe_all'))
async def unsubscribe_all(event):
  """Send a message when the command /unsubscribe_all is issued."""
  # insert chat_id
  chat_id = event.message.chat.id
  find = utils.db.user.get_or_none(chat_id=chat_id)
  if not find:# User information doesn't exist
    await event.respond('Failed. Please input /start')
    raise events.StopPropagation
  user_id = find.id

  # Find current subscription data
  _user_subscribe_list = utils.db.connect.execute_sql('select keywords,channel_name,chat_id from user_subscribe_list where user_id = %d and status  = %d' % (user_id,0) ).fetchall()
  if _user_subscribe_list:
    msg = ''
    for keywords,channel_name,chat_id in _user_subscribe_list:
      channel_url = get_channel_url(channel_name,chat_id)
      msg += f'Keyword: {keywords}\nChannel: {channel_url}\n{"---"*12}\n'


    re_update = utils.db.user_subscribe_list.update(status = 1 ).where(utils.User_subscribe_list.user_id == user_id)# Update status
    re_update = re_update.execute()# Update success returns 1, regardless of whether repeated execution
    if re_update:
      # await event.respond('success unsubscribe_all:\n' + msg,link_preview = False,parse_mode = None)
      text, entities = html.parse('success unsubscribe_all:\n' + msg)# Parse large text, send in batches to avoid output errors
      for text, entities in telethon_utils.split_text(text, entities):
        await event.respond(text,formatting_entities=entities)

  else:
    await event.respond('not found unsubscribe list')
  raise events.StopPropagation


@bot.on(events.NewMessage(pattern='/unsubscribe_id'))
async def unsubscribe_id(event):
  '''
  Unsubscribe by id
  '''
  chat_id = event.message.chat.id
  find = utils.db.user.get_or_none(chat_id=chat_id)
  user_id = find
  if not find:# User information doesn't exist
    await event.respond('Failed. Please input /start')
    raise events.StopPropagation
  text = event.message.text
  text = text.replace('，',',')# 替换掉中文逗号
  text = regex.sub(r'\s*,\s*',',',text) # 确保英文逗号间隔中间都没有空格  如 "https://t.me/xiaobaiup, https://t.me/com9ji"
  splitd = [i for i in regex.split(r'\s+',text) if i]# 删除空元素
  if len(splitd) > 1:
    ids = [int(i) for i in splitd[1].split(',') if i.isnumeric()]
    if not ids:
      await event.respond('Please input your unsubscribe_id. \ne.g. `/unsubscribe_id 123,321`')
      raise events.StopPropagation
    result = []
    for i in ids:
      re_update = utils.db.user_subscribe_list.update(status = 1 ).where(utils.User_subscribe_list.id == i,utils.User_subscribe_list.user_id == user_id)# Update status
      re_update = re_update.execute()# Update success returns 1, regardless of whether repeated execution
      if re_update:
        result.append(i)
    await event.respond('success unsubscribe id:{}'.format(result if result else 'None'))
  elif len(splitd) < 2:
    await event.respond('Input the subscription id that needs **unsubscribe**:\n\nEnter the subscription id of the channel where ** unsubscribe **is required:')
    cache.set('status_{}'.format(chat_id),{'current_status':'/unsubscribe_id ids','record_value':None},expire=5*60)# Record input keyword
    raise events.StopPropagation
  else:
    await event.respond('not found id')
  raise events.StopPropagation


@bot.on(events.NewMessage(pattern='/unsubscribe'))
async def unsubscribe(event):
  """Send a message when the command /unsubscribe is issued."""
  # insert chat_id
  chat_id = event.message.chat.id
  find = utils.db.user.get_or_none(chat_id=chat_id)
  user_id = find
  if not find:# User information doesn't exist
    await event.respond('Failed. Please input /start')
    raise events.StopPropagation


  text = event.message.text
  text = text.replace('，',',')# Replace Chinese comma
  text = regex.sub(r'\s*,\s*',',',text) # Ensure no spaces between English commas, e.g. "https://t.me/xiaobaiup, https://t.me/com9ji"
  splitd = [i for i in regex.split(r'\s+',text) if i]# Remove empty elements
  if len(splitd) <= 1:
    await event.respond('Input the keyword that needs **unsubscribe**\n\nEnter a keyword that requires **unsubscribe**')
    cache.set('status_{}'.format(chat_id),{'current_status':'/unsubscribe keywords','record_value':text},expire=5*60)# Set to expire after 5m
  elif len(splitd)  == 3:
    command, keywords, channels = splitd
    result = update_subscribe(user_id,parse_full_command(command, keywords, channels))
    # msg = ''
    # for key,channel in result:
    #   msg += 'keyword:{}  channel:{}\n'.format(key,channel)
    # if msg:
    #   await event.respond('success unsubscribe:\n'+msg,parse_mode = None)
    await event.respond('success unsubscribe.')

  raise events.StopPropagation


# Limit message text length
@bot.on(events.NewMessage(pattern='/setlengthlimit'))
async def setlengthlimit(event):
    blacklist_type = 'length_limit'
    command = r'/setlengthlimit'
    # get chat_id
    chat_id = event.message.chat.id
    find = utils.db.user.get_or_none(chat_id=chat_id)
    user_id = find
    if not find:  # User information doesn't exist
        await event.respond('Failed. Please input /start')
        raise events.StopPropagation

    # parse input
    text = event.message.text
    text = text.replace('，', ',')  # Replace Chinese comma
    text = regex.sub(f'^{command}', '', text).strip()  # Ensure no spaces between English commas
    splitd = [i for i in text.split(',') if i]  # Remove empty elements

    find = utils.db.connect.execute_sql('select id,blacklist_value from user_block_list where user_id = ? and blacklist_type=? ' ,(user_id.id,blacklist_type)).fetchone()
    if not splitd:
      if find is None:
        await event.respond('lengthlimit not found.')
      else:
        await event.respond(f'setlengthlimit `{find[1]}`')
    else: # Pass multiple parameters e.g. /setlengthlimit 123
      if len(splitd) == 1 and splitd[0].isdigit():
        blacklist_value = int(splitd[0])

        if find is None:
          # create entry in UserBlockList
          insert_res = utils.db.user_block_list.create(**{
              'user_id': user_id,
              'channel_name': '',
              'chat_id': '',
              'blacklist_type': blacklist_type,
              'blacklist_value': blacklist_value,
              'create_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              'update_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
          })

          if insert_res:
              await event.respond(f'Success lengthlimit `{blacklist_value}`')
          else:
              await event.respond(f'Failed lengthlimit `{blacklist_value}`')
        else:
          update_query = utils.db.user_block_list.update(blacklist_value = blacklist_value,update_time=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')).where(utils.User_block_list.id == find[0])# Update status
          update_result = update_query.execute()# Update success returns 1, regardless of whether repeated execution
          if update_result:
            await event.respond(f'Success lengthlimit `{blacklist_value}`')
          else:
            await event.respond(f'Failed lengthlimit `{blacklist_value}`')
    raise events.StopPropagation


@bot.on(events.NewMessage(pattern='/help'))
async def start(event):
  await event.respond('''

Purpose: Subscribe to channel messages based on keywords. Support groups

Support multi-keyword and multi-channel subscription, use comma `,` separator

Use space between keywords and channels

Main commands:

 - Subscribe operation

  /subscribe  keyword1,keyword2 tianfutong,xiaobaiup

  /subscribe  keyword1,keyword2 https://t.me/tianfutong,https://t.me/xiaobaiup

 - Unsubscribe

  /unsubscribe  keyword1,keyword2 https://t.me/tianfutong,https://t.me/xiaobaiup

 - Unsubscribe by id

  /unsubscribe_id  1,2

 - Unsubscribe all

  /unsubscribe_all

 - Show all subscription list

  /list

---
Purpose: Subscribe to channel messages based on keywords. Support groups

Multi-keyword and multi-channel subscription support, using comma `,` interval.

Use space between keywords and channels

Main command:

/subscribe  keyword1,keyword2 tianfutong,xiaobaiup
/subscribe  keyword1,keyword2 https://t.me/tianfutong,https://t.me/xiaobaiup

/unsubscribe  keyword1,keyword2 https://t.me/tianfutong,https://t.me/xiaobaiup

/unsubscribe_id  1,2

/unsubscribe_all

/list

  ''')
  raise events.StopPropagation


# Delete currently recorded user status
@bot.on(events.NewMessage(pattern='/cancel'))
async def cancel(event):
  chat_id = event.message.chat.id
  _ = cache.delete('status_{}'.format(chat_id))
  if _ :
    await event.respond('success cancel.')
  raise events.StopPropagation

# Query all subscriptions of current user
@bot.on(events.NewMessage(pattern='/list'))
async def _list(event):
  chat_id = event.message.chat.id
  find = utils.db.user.get_or_none(**{
      'chat_id':chat_id,
  })
  if find:
    find = utils.db.connect.execute_sql('select id,keywords,channel_name,chat_id from user_subscribe_list where user_id = %d and status  = %d' % (find.id,0) ).fetchall()
    if find:
      msg = ''
      for sub_id,keywords,channel_name,chat_id in find:
        _type = 'regex' if is_regex_str_fuzzy(keywords) else 'keyword'
        channel_url = get_channel_url(channel_name,chat_id)

        channel_entity = None # TODO Don't execute entity information read, otherwise will be unresponsive
        # _entity = int(chat_id) if chat_id else channel_name
        # # channel_entity1 = await client.get_entity('tianfutong')
        # # channel_entity2 = await client.get_entity('@tianfutong')
        # # channel_entity3 = await client.get_entity(-1001242421091)
        # # channel_entity4 = await client.get_entity(1242421091)
        # try:
        #   channel_entity = await client.get_entity(_entity)# Get channel related information
        # except ValueError as _e:# Channel doesn't exist error
        #   pass
        #   # logger.info(f'delete user_subscribe_list channel id:{sub_id} _entity:{_entity}')
        #   # re_update = utils.db.user_subscribe_list.update(status = 1 ).where(utils.User_subscribe_list.id == sub_id)
        #   # re_update.execute()
        #   class channel_entity: username='';title=''

        channel_title = ''
        if channel_entity and channel_entity.title:channel_title = f'channel title: {channel_entity.title}\n'

        if channel_name:
          if channel_entity:
            if channel_entity.username:
              if channel_entity.username != channel_name:
                channel_name += '\t[CHANNEL NAME EXPIRED]'# Mark channel name expired
                # channel_name = '' # Don't display
                logger.info(f'channel username:{channel_name} expired.')
            else:
              channel_name += '\t[CHANNEL NONE EXPIRED]'# Mark channel name expired. Currently doesn't exist
              # channel_name = '' # Don't display
              logger.info(f'channel username:{channel_name} expired. current none')
        elif chat_id:# Only chat_id
          if channel_entity and channel_entity.username:
            channel_name = channel_entity.username
            logger.info(f'channel chat_id:{chat_id} username:{channel_name}')

        channel_username = ''
        if channel_entity:# Only display channel name if entity information exists
          if channel_name:
            channel_username = f'channel username: {channel_name}\n'

        channel_url = f'<a href="{channel_url}-1">{"https://t.me/"+channel_name if channel_name else channel_url}</a>'
        msg += build_sublist_msg(sub_id,_type,keywords,channel_url,channel_title,channel_username)

      text, entities = html.parse(msg)# 解析超大文本 分批次发送 避免输出报错
      for text, entities in telethon_utils.split_text(text, entities):
        # await client.send_message(chat, text, formatting_entities=entities)
        await event.respond(text,formatting_entities=entities)
    else:
      await event.respond('not found list')
  else:
    await event.respond('please /start')
  raise events.StopPropagation


# Unified handling method for other messages
@bot.on(events.NewMessage)
async def common(event):
  """Echo the user message."""
  chat_id = event.message.chat.id
  text = event.text
  text = text.replace('，',',')# Replace Chinese comma
  text = regex.sub(r'\s*,\s*',',',text) # Ensure no spaces between English commas, e.g. "https://t.me/xiaobaiup, https://t.me/com9ji"

  find = cache.get('status_{}'.format(chat_id))
  if find:

    # Execute subscription
    if find['current_status'] == '/subscribe keywords':# Currently inputting keyword
      await event.respond('Input the channel url or name to subscribe:\n\nEnter the url or name of the channel to subscribe to:')
      cache.set('status_{}'.format(chat_id),{'current_status':'/subscribe channels','record_value':find['record_value'] + ' ' + text},expire=5*60)# Record input keyword
      raise events.StopPropagation
    elif find['current_status'] == '/subscribe channels':# Currently inputting channel
      full_command = find['record_value'] + ' ' + text
      splitd = [i for i in regex.split(r'\s+',full_command) if i]# Remove empty elements
      if len(splitd)  != 3:
        await event.respond('Keywords should not contain spaces, you can use regex to solve this\n\nThe keyword must not contain Spaces.')
        raise events.StopPropagation
      command, keywords, channels = splitd
      user_id = utils.db.user.get_or_none(chat_id=chat_id)
      result = await join_channel_insert_subscribe(user_id,parse_full_command(command, keywords, channels))
      if isinstance(result,str):
        await event.respond(result,parse_mode = None) # Show error message
      else:
        msg = ''
        for subscribeid,key,channel,_chat_id in result:
          if _chat_id:
            _chat_id, peer_type = telethon_utils.resolve_id(int(_chat_id))

          if not channel:
            channel = f'<a href="t.me/c/{_chat_id}/-1">{_chat_id}</a>'
          msg += build_sublist_msg(subscribeid,'Keywords',key,channel)

        if msg:
          # await event.respond('success subscribe:\n'+msg,parse_mode = None)
          msg = 'success subscribe:\n\n'+msg
          text, entities = html.parse(msg)# Parse large text, send in batches to avoid output errors
          for text, entities in telethon_utils.split_text(text, entities):
            await event.respond(text,formatting_entities=entities)

      cache.delete('status_{}'.format(chat_id))
      raise events.StopPropagation

    # Unsubscribe
    elif find['current_status'] == '/unsubscribe keywords':# Currently inputting keyword
      await event.respond('Input the channel url or name that needs **unsubscribe**:\n\nEnter the url or name of the channel where ** unsubscribe **is required:')
      cache.set('status_{}'.format(chat_id),{'current_status':'/unsubscribe channels','record_value':find['record_value'] + ' ' + text},expire=5*60)# Record input keyword
      raise events.StopPropagation
    elif find['current_status'] == '/unsubscribe channels':# Currently inputting channel
      full_command = find['record_value'] + ' ' + text
      splitd = [i for i in regex.split(r'\s+',full_command) if i]# Remove empty elements
      if len(splitd)  != 3:
        await event.respond('Keywords should not contain spaces, you can use regex to solve this\n\nThe keyword must not contain Spaces.')
        raise events.StopPropagation
      command, keywords, channels = splitd
      user_id = utils.db.user.get_or_none(chat_id=chat_id)
      result = update_subscribe(user_id,parse_full_command(command, keywords, channels))
      # msg = ''
      # for key,channel in result:
      #   msg += '{},{}\n'.format(key,channel)
      # if msg:
      #   await event.respond('success:\n'+msg,parse_mode = None)
      await event.respond('success unsubscribe..')

      cache.delete('status_{}'.format(chat_id))
      raise events.StopPropagation
    elif find['current_status'] == '/unsubscribe_id ids':# Currently inputting subscription id
      splitd =  text.strip().split(',')
      user_id = utils.db.user.get_or_none(chat_id=chat_id)
      result = []
      for i in splitd:
        if not i.isdigit():
          continue
        i = int(i)
        re_update = utils.db.user_subscribe_list.update(status = 1 ).where(utils.User_subscribe_list.id == i,utils.User_subscribe_list.user_id == user_id)# Update status
        re_update = re_update.execute()# Update success returns 1, regardless of whether repeated execution
        if re_update:
          result.append(i)
      await event.respond('success unsubscribe id:{}'.format(result if result else 'None'))
  raise events.StopPropagation

async def main():
    """Main entry point - runs health check server and telegram client concurrently"""
    cache.expire()
    print(banner())

    # Start the clients within the async context
    await client.start(phone=account['phone'])
    await bot.start(bot_token=account['bot_token'])

    # Start health check server and telegram client concurrently
    await asyncio.gather(
        run_health_server(),
        client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
