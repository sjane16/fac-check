from django.db import migrations


ROSTER = (
    ('air-conditioning-mechanical', (
        ('george-l-castro', 'George L. Castro'),
        ('rorderick-q-ordinario', 'Rorderick Q. Ordinario'),
        ('alfredo-e-badaguas', 'Alfredo E. Badaguas'),
    )),
    ('carpentry-painting', (
        ('rosanto-h-rosales', 'Rosanto H. Rosales'),
        ('mark-arvin-a-bauzon', 'Mark Arvin A. Bauzon'),
        ('manuel-r-orallo', 'Manuel R. Orallo'),
        ('benjamin-v-escober', 'Benjamin V. Escober'),
        ('mederick-l-maniego', 'Mederick L. Maniego'),
        ('john-f-david', 'John F. David'),
        ('fernando-g-pascubillo', 'Fernando G. Pascubillo'),
        ('mark-adrian-remigio', 'Mark Adrian Remigio'),
    )),
    ('electrical', (
        ('joseph-sunny-boy-s-garcia', 'Joseph Sunny Boy S. Garcia'),
        ('percival-d-padilla', 'Percival D. Padilla'),
        ('emmanuel-s-reyes', 'Emmanuel S. Reyes'),
        ('jayson-t-garcia', 'Jayson T. Garcia'),
        ('ramir-r-joaquin', 'Ramir R. Joaquin'),
        ('victor-m-panlaqui', 'Victor M. Panlaqui'),
        ('albert-dominic-e-de-dios', 'Albert Dominic E. De Dios'),
    )),
    ('plumbing', (
        ('emmanuel-b-alivio-jr', 'Emmanuel B. Alivio, Jr.'),
        ('roderick-r-nogueras', 'Roderick R. Nogueras'),
    )),
)


def seed_roster(apps, schema_editor):
    Personnel = apps.get_model('adm', 'Personnel')
    Profile = apps.get_model('user', 'UserProfile')
    db = schema_editor.connection.alias
    for category, members in ROSTER:
        for slug, name in members:
            Personnel.objects.using(db).get_or_create(slug=slug, defaults={'name': name, 'category': category})
    # Preserve any personnel added through the earlier account-linking workflow.
    categories = {category for category, _ in ROSTER}
    for profile in Profile.objects.using(db).filter(role='med_personnel', med_personnel_slug__startswith='account-', med_personnel_category__in=categories).select_related('user'):
        name = f'{profile.user.first_name} {profile.user.last_name}'.strip() or profile.user.email or profile.user.username
        if not Personnel.objects.using(db).filter(name__iexact=name).exists():
            Personnel.objects.using(db).get_or_create(slug=profile.med_personnel_slug, defaults={
                'name': name, 'category': profile.med_personnel_category,
                'is_active': profile.user.is_active,
            })


class Migration(migrations.Migration):
    dependencies = [
        ('adm', '0005_personnel'),
        ('user', '0041_historicaluserprofile_med_personnel_category_and_more'),
    ]
    operations = [migrations.RunPython(seed_roster, migrations.RunPython.noop)]
