from django.core.management.base import BaseCommand, CommandError

from core.menu import MENU_VERSION, apply_menu
from core.models import Restaurant


class Command(BaseCommand):
    help = "Load the built-in showcase menu into a restaurant (by its booking slug). Runs once per menu version."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--force", action="store_true", help="Load again even if this menu version is already in.")

    def handle(self, *args, **opts):
        r = Restaurant.objects.filter(slug=opts["slug"]).first()
        if r is None:
            if opts["force"]:
                raise CommandError(f"No restaurant with slug {opts['slug']!r}.")
            self.stdout.write("No such restaurant, skipping.")
            return
        loaded = apply_menu(r, force=opts["force"])
        self.stdout.write(f"Menu {MENU_VERSION} loaded into {r.name}." if loaded else f"{r.name} already has menu {MENU_VERSION}.")
