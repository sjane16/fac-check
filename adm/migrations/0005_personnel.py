from django.db import migrations, models
import django.db.models.functions.text


class Migration(migrations.Migration):
    dependencies = [('adm', '0004_alter_room_options_room_order')]

    operations = [migrations.CreateModel(
        name='Personnel',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('slug', models.SlugField(max_length=64, unique=True)),
            ('name', models.CharField(max_length=100)),
            ('category', models.CharField(choices=[('air-conditioning-mechanical', 'Air-Conditioning and Mechanical'), ('carpentry-painting', 'Carpentry and Painting'), ('electrical', 'Electrical'), ('plumbing', 'Plumbing')], max_length=40)),
            ('is_active', models.BooleanField(default=True)),
            ('previous_names', models.JSONField(blank=True, default=list)),
        ],
        options={
            'ordering': ['pk'],
            'constraints': [models.UniqueConstraint(django.db.models.functions.text.Lower('name'), name='unique_personnel_name')],
        },
    )]
