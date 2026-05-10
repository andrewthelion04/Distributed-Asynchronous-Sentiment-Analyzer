from django.contrib import admin
from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display  = ('id', 'status', 'sentiment', 'created_at', 'short_text')
    list_filter   = ('status', 'sentiment')
    search_fields = ('text',)
    readonly_fields = ('created_at', 'updated_at')

    def short_text(self, obj):
        return obj.text[:80]
    short_text.short_description = 'Text'
