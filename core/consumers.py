from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Table


class _Live(AsyncWebsocketConsumer):
    groups_joined = ()

    async def connect(self):
        self.groups_joined = await self.find_groups()
        if not self.groups_joined:
            await self.close()
            return
        for g in self.groups_joined:
            await self.channel_layer.group_add(g, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        for g in self.groups_joined:
            await self.channel_layer.group_discard(g, self.channel_name)

    async def changed(self, event):
        # The message carries no data: the screen fetches fresh state from the server.
        await self.send(text_data='{"changed": true}')


class StaffConsumer(_Live):
    """Staff and kitchen screens: one group per restaurant, login required."""

    async def find_groups(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            return ()
        rid = await self._restaurant_id(user)
        return (f"rest-{rid}",) if rid else ()

    @database_sync_to_async
    def _restaurant_id(self, user):
        profile = getattr(user, "profile", None)
        return profile.restaurant_id if profile else None


class GuestConsumer(_Live):
    """Guest page: a group for the table, found by its random code, and one for the restaurant's quiz."""

    async def find_groups(self):
        found = await self._table(self.scope["url_route"]["kwargs"]["code"])
        return (f"table-{found[0]}", f"quiz-{found[1]}") if found else ()

    @database_sync_to_async
    def _table(self, code):
        return Table.objects.filter(code=code).values_list("pk", "restaurant_id").first()
