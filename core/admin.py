from django.contrib import admin

from . import models


class SizeInline(admin.TabularInline):
    model = models.DishSize
    extra = 1


@admin.register(models.Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "station", "prep_minutes", "art", "photo", "featured", "available")
    list_filter = ("restaurant", "category", "station", "available")
    inlines = [SizeInline]


@admin.register(models.Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("restaurant", "number", "x", "y", "round", "code")
    readonly_fields = ("code",)


class ItemInline(admin.TabularInline):
    model = models.OrderItem
    extra = 0


@admin.register(models.Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "restaurant", "table", "status", "created_at")
    list_filter = ("restaurant", "status")
    inlines = [ItemInline]


class PaymentInline(admin.TabularInline):
    model = models.Payment
    extra = 0


@admin.register(models.Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("id", "restaurant", "table", "status", "split", "created_at", "paid_at")
    list_filter = ("restaurant", "status")
    inlines = [PaymentInline]


admin.site.register(models.Restaurant)
admin.site.register(models.StaffProfile)
admin.site.register(models.Category)
admin.site.register(models.ServiceRequest)
admin.site.register(models.Deal)
admin.site.register(models.Member)
admin.site.register(models.Review)
admin.site.register(models.QuizQuestion)
