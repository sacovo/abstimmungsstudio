"""Moderation surface. Unfold is already configured, so this is the whole UI."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from ticker.models import TickerEvent, TickerNote, TickerPost


@admin.register(TickerPost)
class TickerPostAdmin(ModelAdmin):
    list_display = ("created_at", "vorlage", "status", "author", "text")
    list_filter = ("status", "author", "vorlage__region", "vorlage__tag")
    search_fields = ("text", "vorlage__name")
    list_editable = ("status",)
    readonly_fields = ("created_at", "snapshot", "event", "author")
    ordering = ("-created_at",)

    actions = ["retract", "republish"]

    @admin.action(description="Zurückziehen")
    def retract(self, request, queryset):
        queryset.update(status=TickerPost.Status.RETRACTED)

    @admin.action(description="Wieder publizieren")
    def republish(self, request, queryset):
        queryset.update(status=TickerPost.Status.PUBLISHED)

    def save_model(self, request, obj, form, change):
        if change and "text" in form.changed_data:
            from django.utils import timezone

            obj.edited_at = timezone.now()
            obj.edited_by = request.user.get_username()
        super().save_model(request, obj, form, change)


@admin.register(TickerEvent)
class TickerEventAdmin(ModelAdmin):
    list_display = ("detected_at", "vorlage", "kind", "severity", "status", "summary")
    list_filter = ("status", "kind", "severity", "vorlage__region")
    search_fields = ("summary", "key")
    readonly_fields = ("payload",)
    ordering = ("-detected_at",)


@admin.register(TickerNote)
class TickerNoteAdmin(ModelAdmin):
    list_display = ("created_at", "vorlage", "resolved", "author", "text")
    list_filter = ("resolved", "author")
    list_editable = ("resolved",)
    ordering = ("-created_at",)
