from django.core.cache import cache

from user.constants import MED_PERSONNEL_CATEGORY_BY_SLUG, MED_PERSONNEL_CATEGORY_CHOICES
from .models import Personnel

CACHE_KEY = 'med_personnel_roster_v2'
CACHE_SECONDS = 60


def personnel_options(include_inactive=False):
    options = cache.get(CACHE_KEY)
    if options is None:
        options = [
            {
                'slug': item.slug, 'name': item.name,
                'category_slug': item.category,
                'category': MED_PERSONNEL_CATEGORY_BY_SLUG[item.category],
                'is_active': item.is_active, 'previous_names': item.previous_names,
            }
            for item in Personnel.objects.all()
        ]
        cache.set(CACHE_KEY, options, CACHE_SECONDS)
    return options if include_inactive else [item for item in options if item['is_active']]


def personnel_name_map():
    return {
        name: item['name']
        for item in personnel_options(include_inactive=True)
        for name in [item['name'], *item['previous_names']]
    }


def personnel_groups():
    options = personnel_options()
    return [
        {'slug': slug, 'name': name, 'personnel': [item for item in options if item['category_slug'] == slug]}
        for slug, name in MED_PERSONNEL_CATEGORY_CHOICES
    ]


def clear_personnel_cache():
    cache.delete(CACHE_KEY)
