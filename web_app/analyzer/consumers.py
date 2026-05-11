import asyncio
import os

import redis.asyncio as aioredis
from channels.generic.websocket import AsyncWebsocketConsumer

REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self._channel = self.scope['url_route']['kwargs']['channel']
        self._redis_ch = f'chat_updates:{self._channel}'

        await self.accept()

        self._redis = aioredis.from_url(f'redis://{REDIS_HOST}')
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._redis_ch)
        self._listener = asyncio.create_task(self._listen())

    async def disconnect(self, close_code):
        if hasattr(self, '_listener'):
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
        if hasattr(self, '_pubsub'):
            await self._pubsub.unsubscribe(self._redis_ch)
            await self._pubsub.aclose()
        if hasattr(self, '_redis'):
            await self._redis.aclose()

    async def _listen(self):
        async for message in self._pubsub.listen():
            if message['type'] == 'message':
                await self.send(message['data'].decode('utf-8'))

    async def receive(self, text_data=None, bytes_data=None):
        pass
