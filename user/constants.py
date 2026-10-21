PRIORITY_SLA_HOURS = {
    'critical': 3,
    'high': 24,
    'standard': 72,
}

MED_PERSONNEL_GROUPS = (
    ('air-conditioning-mechanical', 'Air-Conditioning and Mechanical', (
        ('george-l-castro', 'George L. Castro'),
        ('rorderick-q-ordinario', 'Rorderick Q. Ordinario'),
        ('alfredo-e-badaguas', 'Alfredo E. Badaguas'),
    )),
    ('carpentry-painting', 'Carpentry and Painting', (
        ('rosanto-h-rosales', 'Rosanto H. Rosales'),
        ('mark-arvin-a-bauzon', 'Mark Arvin A. Bauzon'),
        ('manuel-r-orallo', 'Manuel R. Orallo'),
        ('benjamin-v-escober', 'Benjamin V. Escober'),
        ('mederick-l-maniego', 'Mederick L. Maniego'),
        ('john-f-david', 'John F. David'),
        ('fernando-g-pascubillo', 'Fernando G. Pascubillo'),
        ('mark-adrian-remigio', 'Mark Adrian Remigio'),
    )),
    ('electrical', 'Electrical', (
        ('joseph-sunny-boy-s-garcia', 'Joseph Sunny Boy S. Garcia'),
        ('percival-d-padilla', 'Percival D. Padilla'),
        ('emmanuel-s-reyes', 'Emmanuel S. Reyes'),
        ('jayson-t-garcia', 'Jayson T. Garcia'),
        ('ramir-r-joaquin', 'Ramir R. Joaquin'),
        ('victor-m-panlaqui', 'Victor M. Panlaqui'),
        ('albert-dominic-e-de-dios', 'Albert Dominic E. De Dios'),
    )),
    ('plumbing', 'Plumbing', (
        ('emmanuel-b-alivio-jr', 'Emmanuel B. Alivio, Jr.'),
        ('roderick-r-nogueras', 'Roderick R. Nogueras'),
    )),
)

MED_PERSONNEL_OPTIONS = tuple(
    {'slug': slug, 'name': name, 'category_slug': category_slug, 'category': category}
    for category_slug, category, members in MED_PERSONNEL_GROUPS
    for slug, name in members
)
MED_PERSONNEL_CATEGORY_CHOICES = tuple(
    (category_slug, category)
    for category_slug, category, _ in MED_PERSONNEL_GROUPS
)
MED_PERSONNEL_CATEGORY_BY_SLUG = dict(MED_PERSONNEL_CATEGORY_CHOICES)
MED_PERSONNEL_BY_SLUG = {item['slug']: item for item in MED_PERSONNEL_OPTIONS}
