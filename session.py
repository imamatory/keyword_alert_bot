from telethon import TelegramClient
from telethon.sessions import StringSession
from config import config


def generate_reader_session_id(account_config):
    client = TelegramClient(StringSession(), account_config['api_id'], account_config['api_hash'])
    client.start(phone=account_config['phone'])
    return client.session.save()


if __name__ == '__main__':
    account_config = config['account']
    reader_session_id = generate_reader_session_id(account_config)
    print(f'Reader session ID: {reader_session_id}')