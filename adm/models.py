from django.db import models
from django.db.models.functions import Lower

from user.constants import MED_PERSONNEL_CATEGORY_CHOICES


class Building(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10)
    has_floors = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Floor(models.Model):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='floors')
    floor_number = models.IntegerField()
    label = models.CharField(max_length=100)

    class Meta:
        ordering = ['floor_number']
        unique_together = ['building', 'floor_number']

    def __str__(self):
        return f"{self.building.name} - Floor {self.floor_number}"


class Room(models.Model):
    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name='rooms')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    order = models.PositiveIntegerField(default=0, help_text = "Controls the display order in dropdown.")

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Personnel(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=40, choices=MED_PERSONNEL_CATEGORY_CHOICES)
    is_active = models.BooleanField(default=True)
    # Assignment snapshots keep their original name; aliases preserve rating identity.
    previous_names = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(Lower('name'), name='unique_personnel_name')]

    def __str__(self):
        return self.name
    
