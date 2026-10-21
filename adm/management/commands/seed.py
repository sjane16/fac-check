import shutil
import os
from django.conf import settings
from django.core.management.base import BaseCommand
from adm.models import Building, Floor, Room


class Command(BaseCommand):
    help = 'Seed buildings, floors and rooms'

    def handle(self, *args, **kwargs):
        # ── Prevent duplicates ──────────────────────────────
        if Building.objects.exists():
            self.stdout.write(self.style.WARNING('Existing data found! Clearing and reseeding...'))
            Room.objects.all().delete()
            Floor.objects.all().delete()
            Building.objects.all().delete()

        # ── Buildings ───────────────────────────────────────
        mb = Building.objects.create(name='Main Building', code='MB')
        nb = Building.objects.create(name='New Building', code='NB')
        ab = Building.objects.create(name='Administrative Building', code='AB')  

        # ── AB Floors ──────────────────────────────────────
        ab_f1 = Floor.objects.create(
            building=ab, floor_number=1,
            label='Ground Floor'
        )
        ab_f2 = Floor.objects.create(
            building=ab, floor_number=2,
            label='Second Floor',
        )

        # ── NB Floors ───────────────────────────────────────
        nb_f1 = Floor.objects.create(
            building=nb, floor_number=1,
            label='Ground Floor',
        )
        nb_f2 = Floor.objects.create(
            building=nb, floor_number=2,
            label='Second Floor',
        )
        nb_f3 = Floor.objects.create(
            building=nb, floor_number=3,
            label='Third Floor',
        )
        nb_f4 = Floor.objects.create(
            building=nb, floor_number=4,
            label='Fourth Floor',
        )
        nb_f5 = Floor.objects.create(
            building=nb, floor_number=5,
            label='Roof Deck',
        )

        # ── MB Floors ───────────────────────────────────────
        mb_f1 = Floor.objects.create(
            building=mb, floor_number=1,
            label='Ground Floor',
        )
        mb_f2 = Floor.objects.create(
            building=mb, floor_number=2,
            label='Second Floor',
        )
        mb_f3 = Floor.objects.create(
            building=mb, floor_number=3,
            label='Third Floor',
        )
        mb_f4 = Floor.objects.create(
            building=mb, floor_number=4,
            label='Fourth Floor'
        )
        mb_f5 = Floor.objects.create(
            building=mb, floor_number=5,
            label='Roof Deck'
        )

        # ══════════════════════════════════════════════════════
        # ADMINISTRATIVE BUILDING ROOMS
        # ══════════════════════════════════════════════════════

        # Ground Floor
        Room.objects.create(floor=ab_f1, name='University Health Services', code='AB_UHS', order=1)
        Room.objects.create(floor=ab_f1, name='Maintenance and Engineering Office', code='LLB_MEO', order=2)
        Room.objects.create(floor=ab_f1, name='Supply and Equipment Office', code='LLB_SEO', order=3)
        Room.objects.create(floor=ab_f1, name='Registrar', code='LLB_REG', order=4)
        Room.objects.create(floor=ab_f1, name='Cash Management Division', code='LLB_CMD', order=5)
        Room.objects.create(floor=ab_f1, name='Administrative Offices', code='LLB_ADMIN', order=6)

        # Second Floor
        Room.objects.create(floor=ab_f2, name='Library', code='AB_LIB', order=1)
        Room.objects.create(floor=ab_f2, name='Supreme Student Government', code='AB_SSG', order=2)
        Room.objects.create(floor=ab_f2, name='National Service Training Program', code='AB_NSTP', order=3)
        Room.objects.create(floor=ab_f2, name='Athletics and Cultural Engagement Division', code='AB_ACED', order=4)
        Room.objects.create(floor=ab_f2, name='Records Office', code='AB_RECORDS', order=5)
        Room.objects.create(floor=ab_f2, name='Student Lounge', code='AB_LOUNGE', order=6)

        # ══════════════════════════════════════════════════════
        # NEW BUILDING ROOMS
        # ══════════════════════════════════════════════════════

        # Ground Floor
        Room.objects.create(floor=nb_f1, name='ROTC', code='NB_ROTC', order=1)
        Room.objects.create(floor=nb_f1, name='Dulaan', code='NB_DULAAN', order=2)
        Room.objects.create(floor=nb_f1, name='Chorale', code='NB_CHORALE', order=3)
        Room.objects.create(floor=nb_f1, name='Accounting Division', code='NB_ACCTG', order=4)
        Room.objects.create(floor=nb_f1, name='Planning and Budget Division', code='NB_PBD', order=5)
        Room.objects.create(floor=nb_f1, name='Payroll', code='NB_PAYROLL', order=6)
        Room.objects.create(floor=nb_f1, name='PROCUREMENT', code='NB_PROC', order=7)
        Room.objects.create(floor=nb_f1, name='Human Resources Management and Development Division', code='NB_HRMDD', order=8)
        Room.objects.create(floor=nb_f1, name='Records', code='NB_REC', order=9)
        Room.objects.create(floor=nb_f1, name='Office of the VP for Comptrollership', code='NB_VPC', order=10)
        Room.objects.create(floor=nb_f1, name='Office of the VP for Administration', code='NB_VPA', order=11)
        Room.objects.create(floor=nb_f1, name='Storage Room', code='NB_STORAGE', order=12)
        Room.objects.create(floor=nb_f1, name='Employee\'s Comfort Room', code='NB_FCR', order=13)

        # Second Floor
        Room.objects.create(floor=nb_f2, name='NB201 - CBA FACULTY ROOM', code='NB201', order=1)
        Room.objects.create(floor=nb_f2, name='NB202', code='NB202', order=2)
        Room.objects.create(floor=nb_f2, name='NB203', code='NB203', order=3)
        Room.objects.create(floor=nb_f2, name='NB204', code='NB204', order=4)
        Room.objects.create(floor=nb_f2, name='NB205', code='NB205', order=5)
        Room.objects.create(floor=nb_f2, name='NB206 - Registrar\'s Storage Room', code='NB206', order=6)
        Room.objects.create(floor=nb_f2, name='Male - Comfort Room', code='NBMALECR', order=7)
        Room.objects.create(floor=nb_f2, name='Female - Comfort Room', code='NBFEMALECR', order=8)

        # Third Floor
        Room.objects.create(floor=nb_f3, name='NB301', code='NB301', order=1)
        Room.objects.create(floor=nb_f3, name='NB302', code='NB302', order=2)
        Room.objects.create(floor=nb_f3, name='NB303', code='NB303', order=3)
        Room.objects.create(floor=nb_f3, name='NB304', code='NB304', order=4)
        Room.objects.create(floor=nb_f3, name='NB305', code='NB305', order=5)
        Room.objects.create(floor=nb_f3, name='NB306', code='NB306', order=6)
        Room.objects.create(floor=nb_f3, name='NB307 - ICTO', code='NB307', order=7)

        # Fourth Floor
        Room.objects.create(floor=nb_f4, name='NB401 - Broadcasting', code='NB401', order=1)
        Room.objects.create(floor=nb_f4, name='NB402', code='NB402', order=2)
        Room.objects.create(floor=nb_f4, name='NB403', code='NB403', order=3)
        Room.objects.create(floor=nb_f4, name='NB404', code='NB404', order=4)
        Room.objects.create(floor=nb_f4, name='NB405 - Physics Laboratory', code='NB405', order=5)
        Room.objects.create(floor=nb_f4, name='NB406 - Chemistry Laboratory', code='NB406', order=6)

        # Roof Deck
        Room.objects.create(floor=nb_f5, name='Roof Deck', code='NB_ROOF', order=1)

        # ══════════════════════════════════════════════════════
        # MAIN BUILDING ROOMS
        # ══════════════════════════════════════════════════════

        # Ground Floor
        Room.objects.create(floor=mb_f1, name='Office of the VP for Academic Affairs', code='MB_VPAA', order=1)
        Room.objects.create(floor=mb_f1, name='Accreditation Room', code='MB_ACCRED', order=2)
        Room.objects.create(floor=mb_f1, name='Quality Assurance and Accreditation', code='MB_QAA', order=3)
        Room.objects.create(floor=mb_f1, name='Office of the University President', code='MB_PRES', order=4)
        Room.objects.create(floor=mb_f1, name='Guidance Counseling and Psychological Services', code='MB_GCPS', order=5)
        Room.objects.create(floor=mb_f1, name='University Research Extension Linkages', code='MB_URELA', order=6)
        Room.objects.create(floor=mb_f1, name='Institute of Graduate and Professional Studies', code='MB_IGPS', order=7)
        Room.objects.create(floor=mb_f1, name='College of Health Sciences', code='MB_CHS', order=8)
        Room.objects.create(floor=mb_f1, name='College of Computer Studies', code='MB_CCS', order=9)
        Room.objects.create(floor=mb_f1, name='College of Education', code='MB_CED', order=10)
        Room.objects.create(floor=mb_f1, name='College of Criminal Justice', code='MB_CCJ', order=11)
        Room.objects.create(floor=mb_f1, name='Admission Office', code='MB_ADMIT', order=12)
        Room.objects.create(floor=mb_f1, name='College of Business and Management', code='MB_CBM', order=13)
        Room.objects.create(floor=mb_f1, name='College of Arts and Sciences', code='MB_CAS', order=14)
        Room.objects.create(floor=mb_f1, name='Security Office', code='MB_SEC', order=15)
        Room.objects.create(floor=mb_f1, name='Office of Student Affairs', code='MB_OSA', order=16)
        Room.objects.create(floor=mb_f1, name='Office of the VP for Student Success and Support Services', code='MB_VPSSSS', order=17)

        # Second Floor
        Room.objects.create(floor=mb_f2, name='M201', code='M201', order=1)
        Room.objects.create(floor=mb_f2, name='M202', code='M202', order=2)
        Room.objects.create(floor=mb_f2, name='M203', code='M203', order=3)
        Room.objects.create(floor=mb_f2, name='M204', code='M204', order=4)
        Room.objects.create(floor=mb_f2, name='M205', code='M205', order=5)
        Room.objects.create(floor=mb_f2, name='M206', code='M206', order=6)
        Room.objects.create(floor=mb_f2, name='M207', code='M207', order=7)
        Room.objects.create(floor=mb_f2, name='Male Comfort Room - Right Wing', code='M_MRW', order=8)
        Room.objects.create(floor=mb_f2, name='Female Comfort Room - Right Wing', code='M_FRW', order=9)
        Room.objects.create(floor=mb_f2, name='M208 - Athletics and Cultural', code='M208', order=10)
        Room.objects.create(floor=mb_f2, name='Palma Hall', code='MB_PALMA', order=11)
        Room.objects.create(floor=mb_f2, name='M209', code='M209', order=12)
        Room.objects.create(floor=mb_f2, name='M210', code='M210', order=13)
        Room.objects.create(floor=mb_f2, name='M211', code='M211', order=14)
        Room.objects.create(floor=mb_f2, name='M212', code='M212', order=15)
        Room.objects.create(floor=mb_f2, name='M213', code='M213', order=16)
        Room.objects.create(floor=mb_f2, name='M214', code='M214', order=17)
        Room.objects.create(floor=mb_f2, name='Male Comfort Room - Left Wing', code='M2_MLW', order=18)
        Room.objects.create(floor=mb_f2, name='Female Comfort Room - Left Wing', code='M2_FLW', order=19)

        # Third Floor
        Room.objects.create(floor=mb_f3, name='M301', code='M301', order=1)
        Room.objects.create(floor=mb_f3, name='M302', code='M302', order=2)
        Room.objects.create(floor=mb_f3, name='M303', code='M303', order=3)
        Room.objects.create(floor=mb_f3, name='M304', code='M304', order=4)
        Room.objects.create(floor=mb_f3, name='M305 – Computer Lab', code='M305', order=5)
        Room.objects.create(floor=mb_f3, name='M306 – Computer Lab', code='M306', order=6)
        Room.objects.create(floor=mb_f3, name='M307 – Computer Lab', code='M307', order=7)
        Room.objects.create(floor=mb_f3, name='Male Comfort Room - Left Wing', code='M3_MLW', order=26)
        Room.objects.create(floor=mb_f3, name='Female Comfort Room - Left Wing', code='M3_FLW', order=27)
        Room.objects.create(floor=mb_f3, name='Moot Court', code='MB_MOOT', order=10)
        Room.objects.create(floor=mb_f3, name='Field Instruction', code='MB_FI', order=12)
        Room.objects.create(floor=mb_f3, name='Office of Legal Aid', code='MB_OLA', order=11)
        Room.objects.create(floor=mb_f3, name='Entrepreneurship Laboratory (Function A)', code='MB_ENTLAB', order=13)
        Room.objects.create(floor=mb_f3, name='Psychology Laboratory (Function B)', code='MB_PSYLAB', order=14)
        Room.objects.create(floor=mb_f3, name='M308 – College of Law Library', code='M308', order=15)
        Room.objects.create(floor=mb_f3, name='Dean College of Law and Administrative Office', code='MDCLAO', order=16)
        Room.objects.create(floor=mb_f3, name='FH302', code='F302', order=17)
        Room.objects.create(floor=mb_f3, name='FH309', code='F309', order=18)
        Room.objects.create(floor=mb_f3, name='Office of Student Internship and Placement', code='MB_OSIPP', order=19)
        Room.objects.create(floor=mb_f3, name='M309', code='M309', order=20)
        Room.objects.create(floor=mb_f3, name='M310', code='M310', order=21)
        Room.objects.create(floor=mb_f3, name='M311', code='M311', order=22)
        Room.objects.create(floor=mb_f3, name='M312', code='M312', order=23)
        Room.objects.create(floor=mb_f3, name='M313', code='M313', order=24)
        Room.objects.create(floor=mb_f3, name='M314', code='M314', order=25)
        Room.objects.create(floor=mb_f3, name='Male Comfort Room - Right Wing', code='M3_MRW', order=8)
        Room.objects.create(floor=mb_f3, name='Female Comfort Room - Right Wing', code='M3_FRW', order=9)

        # Fourth Floor
        Room.objects.create(floor=mb_f4, name='M401', code='M401', order=1)
        Room.objects.create(floor=mb_f4, name='M402', code='M402', order=2)
        Room.objects.create(floor=mb_f4, name='M403', code='M403', order=3)
        Room.objects.create(floor=mb_f4, name='M404', code='M404', order=4)
        Room.objects.create(floor=mb_f4, name='M405', code='M405', order=5)
        Room.objects.create(floor=mb_f4, name='M406', code='M406', order=6)
        Room.objects.create(floor=mb_f4, name='M407', code='M407', order=7)
        Room.objects.create(floor=mb_f4, name='Male Comfort Room - Left Wing', code='M4_MLW', order=25)
        Room.objects.create(floor=mb_f4, name='Female Comfort Room - Left Wing', code='M4_FLW', order=26)
        Room.objects.create(floor=mb_f4, name='Forensic Science Laboratory', code='MB_FORENSIC', order=10)
        Room.objects.create(floor=mb_f4, name='Laboratory Area', code='MB_LABAREA', order=11)
        Room.objects.create(floor=mb_f4, name='Faculty Association', code='MB_FA', order=12)
        Room.objects.create(floor=mb_f4, name='Function Hall F', code='MB_FHF', order=13)
        Room.objects.create(floor=mb_f4, name='General Education Faculty Room', code='MB_GENED', order=14)
        Room.objects.create(floor=mb_f4, name='NRL CHS Skills Laboratory', code='MB_SKILLSLAB', order=15)
        Room.objects.create(floor=mb_f4, name='Physical Therapy Laboratory', code='MB_PTLAB', order=16)
        Room.objects.create(floor=mb_f4, name='La Universidad Official Student Publication', code='MB_OSP', order=17)
        Room.objects.create(floor=mb_f4, name='M408 - Department Chair Lounge', code='M408', order=18)
        Room.objects.create(floor=mb_f4, name='M409', code='M409', order=19)
        Room.objects.create(floor=mb_f4, name='M410', code='M410', order=20)
        Room.objects.create(floor=mb_f4, name='M411', code='M411', order=21)
        Room.objects.create(floor=mb_f4, name='M412', code='M412', order=22)
        Room.objects.create(floor=mb_f4, name='M413', code='M413', order=23)
        Room.objects.create(floor=mb_f4, name='M414 - Lounge', code='M414', order=24)
        Room.objects.create(floor=mb_f4, name='Male Comfort Room - Right Wing', code='M4_MRW', order=8)
        Room.objects.create(floor=mb_f4, name='Female Comfort Room - Right Wing', code='M4_FRW', order=9)

        # Roof Deck
        Room.objects.create(floor=mb_f5, name='Roof Deck', code='MB_ROOF', order=1)

        # ════════════════════════════════════════════════════
        # OTHER FACILITIES
        # ════════════════════════════════════════════════════
        canteen = Building.objects.create(name='Canteen', code='CANT', has_floors=False)
        canteen_f = Floor.objects.create(building=canteen, floor_number=1, label='Main Area')
        Room.objects.create(floor=canteen_f, name='Canteen', code='CANT_MAIN', order=1)

        court = Building.objects.create(name='Covered Court', code='CCRT', has_floors=False)
        court_f = Floor.objects.create(building=court, floor_number=1, label='Main Area')
        Room.objects.create(floor=court_f, name='Covered Court', code='CCRT_MAIN', order=1)
        Room.objects.create(floor=court_f, name='Gender Neutral Comfort Room', code='CCRT_GNCR', order=2)
        Room.objects.create(floor=court_f, name='Female Comfort Room', code='CCRT_FCR', order=3)
        Room.objects.create(floor=court_f, name='Male Comfort Room', code='CCRT_MCR', order=4)
        Room.objects.create(floor=court_f, name='Faculty Comfort Room', code='CCRT_FACCR', order=5)

        garden = Building.objects.create(name='Inner Garden', code='IGDN', has_floors=False)
        garden_f = Floor.objects.create(building=garden, floor_number=1, label='Main Area')
        Room.objects.create(floor=garden_f, name='Inner Garden', code='IGDN_MAIN', order=1)

        parking = Building.objects.create(name='Parking Lot', code='PARK', has_floors=False)
        parking_f = Floor.objects.create(building=parking, floor_number=1, label='Main Area')
        Room.objects.create(floor=parking_f, name='Parking Lot - Near Covered Court', code='PARK_COURT', order=1)
        Room.objects.create(floor=parking_f, name='Parking Lot - Near Exit', code='PARK_EXIT', order=2)


        self.stdout.write(self.style.SUCCESS('Seed data created successfully!'))