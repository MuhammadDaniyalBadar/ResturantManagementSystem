from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Table


class _Live(AsyncWebsocketConsumer):
    group = None

    async def connect(self):
        self.group = await self.find_group()
        if self.group is None:
            await self.close()
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def changed(self, event):
        # The message carries no data: the screen fetches fresh state from the server.
        await self.send(text_data='{"changed": true}')


class StaffConsumer(_Live):
    """Staff and kitchen screens: one group per restaurant, login required."""

    async def find_group(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            return None
        rid = await self._restaurant_id(user)
        return f"rest-{rid}" if rid else None

    @database_sync_to_async
    def _restaurant_id(self, user):
        profile = getattr(user, "profile", None)
        return profile.restaurant_id if profile else None


class GuestConsumer(_Live):
    """Guest page: one group per table, found by the table's random code."""

    async def find_group(self):
        tid = await self._table_id(self.scope["url_route"]["kwargs"]["code"])
        return f"table-{tid}" if tid else None

    @database_sync_to_async
    def _table_id(self, code):
        return Table.objects.filter(code=code).values_list("pk", flat=True).first()
