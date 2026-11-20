from django.contrib import admin

from . import models


class SizeInline(admin.TabularInline):
    model = models.DishSize
    extra = 1


@admin.register(models.Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "station", "prep_minutes", "available")
    list_filter = ("restaurant", "category", "station", "available")
    inlines = [SizeInline]


@admin.register(models.Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("restaurant", "number", "code")
    readonly_fields = ("code",)


class ItemInline(admin.TabularInline):
    model = models.OrderItem
    extra = 0


@admin.register(models.Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "restaurant", "table", "status", "created_at")
    list_filter = ("restaurant", "status")
    inlines = [ItemInline]


admin.site.register(models.Restaurant)
admin.site.register(models.StaffProfile)
admin.site.register(models.Category)
admin.site.register(models.ServiceRequest)
