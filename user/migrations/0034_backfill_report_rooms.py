from collections import defaultdict

from django.db import migrations


def backfill_report_rooms(apps, schema_editor):
    alias = schema_editor.connection.alias
    Room = apps.get_model('adm', 'Room')
    room_matches = defaultdict(list)
    for room in Room.objects.using(alias).select_related('floor__building').iterator(chunk_size=500):
        label = f'{room.floor.building.name}, {room.floor.label}, {room.name}'
        room_matches[label.strip()].append((room.pk, room.floor.building.name, room.name))

    for model_name in ('Report', 'HistoricalReport'):
        model = apps.get_model('user', model_name)
        for report in model.objects.using(alias).iterator(chunk_size=500):
            text = report.location or ''
            matches = room_matches.get(text.strip(), [])
            changes = {}
            if len(matches) == 1:
                room_id, building_name, room_name = matches[0]
                if report.room_id is None:
                    changes['room_id'] = room_id
            else:
                building_name = text.partition(',')[0].strip()
                room_name = text.rpartition(',')[-1].strip()
            if not report.building_name:
                changes['building_name'] = building_name
            if not report.room_name:
                changes['room_name'] = room_name
            if changes:
                model.objects.using(alias).filter(pk=report.pk).update(**changes)


class Migration(migrations.Migration):
    dependencies = [('user', '0033_report_room_snapshots')]
    # Original location text is untouched; reversal retains additive backfill values.
    operations = [migrations.RunPython(backfill_report_rooms, migrations.RunPython.noop)]
