from django.contrib import admin
from django.contrib.auth import get_user_model

from . import models


def _own_restaurant(request):
    return None if request.user.is_superuser else getattr(getattr(request.user, "profile", None), "restaurant", None)


class ScopedChoices:
    """Drop-downs (dish, table, guest, staff...) list only the owner's own restaurant."""

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        r, model = _own_restaurant(request), db_field.related_model
        if r is not None and model is get_user_model():
            kwargs["queryset"] = model.objects.filter(profile__restaurant=r)
        elif r is not None and hasattr(model, "restaurant"):
            kwargs["queryset"] = model.objects.filter(restaurant=r)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class Scoped(ScopedChoices):
    """Owners see and edit only their own restaurant's rows; a superuser sees everything.
    `scope` is the lookup from the model to its restaurant."""

    scope = "restaurant"

    def _restaurant(self, request):
        return _own_restaurant(request)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        r = self._restaurant(request)
        return qs.filter(**{self.scope: r}) if r else qs.none()

    def get_exclude(self, request, obj=None):
        # the restaurant is filled in on save, so an owner never picks another one
        hide = () if request.user.is_superuser or self.scope != "restaurant" else ("restaurant",)
        return tuple(super().get_exclude(request, obj) or ()) + hide

    def get_list_filter(self, request):
        return [f for f in super().get_list_filter(request) if request.user.is_superuser or f != "restaurant"]

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and self.scope == "restaurant":
            obj.restaurant = self._restaurant(request)
        super().save_model(request, obj, form, change)


class SizeInline(ScopedChoices, admin.TabularInline):
    model = models.DishSize
    extra = 1


class RecipeInline(ScopedChoices, admin.TabularInline):
    model = models.RecipeItem
    extra = 1
    verbose_name = "ingredient used per portion"
    verbose_name_plural = "recipe (stock is taken when the order goes to the kitchen)"


@admin.register(models.Dish)
class DishAdmin(Scoped, admin.ModelAdmin):
    list_display = ("name", "category", "price", "diet", "badge", "station", "prep_minutes", "photo", "featured", "available", "retired")
    list_filter = ("restaurant", "category", "station", "available")
    list_editable = ("available",)
    inlines = [SizeInline, RecipeInline]


@admin.register(models.Table)
class TableAdmin(Scoped, admin.ModelAdmin):
    list_display = ("number", "x", "y", "round", "code")
    readonly_fields = ("code",)

    def has_add_permission(self, request):
        r = _own_restaurant(request)
        limit = r.plan_info["tables"] if r else None
        if limit is not None and r.tables.count() >= limit:
            return False  # the plan is full; upgrading lifts the limit
        return super().has_add_permission(request)


class ItemInline(ScopedChoices, admin.TabularInline):
    model = models.OrderItem
    extra = 0


@admin.register(models.Order)
class OrderAdmin(Scoped, admin.ModelAdmin):
    list_display = ("id", "table", "status", "created_at", "cancelled_by")
    list_filter = ("restaurant", "status")
    inlines = [ItemInline]


class PaymentInline(ScopedChoices, admin.TabularInline):
    model = models.Payment
    extra = 0


@admin.register(models.Bill)
class BillAdmin(Scoped, admin.ModelAdmin):
    list_display = ("id", "table", "status", "split", "created_at", "paid_at")
    list_filter = ("restaurant", "status")
    inlines = [PaymentInline]


@admin.register(models.Restaurant)
class RestaurantAdmin(Scoped, admin.ModelAdmin):
    scope = "pk"

    def get_queryset(self, request):
        qs = admin.ModelAdmin.get_queryset(self, request)
        r = self._restaurant(request)
        return qs if request.user.is_superuser else qs.filter(pk=r.pk if r else None)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(models.StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    """Owners manage their team on the Team page; here only a superuser may link logins to restaurants."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    has_view_permission = has_add_permission = has_change_permission = has_delete_permission = lambda self, request, obj=None: request.user.is_superuser


@admin.register(models.Review)
class ReviewAdmin(Scoped, admin.ModelAdmin):
    scope = "bill__restaurant"
    list_display = ("stars", "comment", "guest", "created_at")


for model in (models.Category, models.ServiceRequest, models.Deal, models.Member, models.QuizQuestion, models.Ingredient, models.Reservation):
    admin.site.register(model, type(model.__name__ + "Admin", (Scoped, admin.ModelAdmin), {}))
