from django.contrib import admin
from .models import Building, Floor, Room
# Register your models here.

@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'has_floors']

@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ['building', 'floor_number', 'label']
    list_filter = ['building']

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'floor']
    list_filter = ['floor__building', 'floor']
    search_fields = ['name', 'code']
