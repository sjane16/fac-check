from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from user.models import UserProfile, invalidate_trusted_devices


class Command(BaseCommand):
    help = ("Make an account the Head Admin (the only one who can open User Management), "
            "or remove Head Admin with --remove. Head Admin can't be given from the website.")

    def add_arguments(self, parser):
        parser.add_argument('email', help="Email of the account")
        parser.add_argument('--remove', action='store_true', help="Remove Head Admin from this account")

    def handle(self, *args, email, remove, **options):
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        if not user:
            raise CommandError(f"No account with the email {email}.")
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'role': 'admin'})

        if remove:
            profile.is_head_admin = False
            profile.save(update_fields=['is_head_admin'])
            self.stdout.write(self.style.SUCCESS(f"{user.email} is no longer the Head Admin (still a regular admin)."))
            return

        if not user.is_active:
            raise CommandError(f"{user.email} is not active. Reactivate or verify the account first.")
        if not user.is_staff:
            # The Head Admin is also an admin
            user.is_staff = True
            user.save(update_fields=['is_staff'])
        profile.is_head_admin = True
        profile.save(update_fields=['is_head_admin'])
        invalidate_trusted_devices(user)  # sign in again with an email code

        others = UserProfile.objects.filter(is_head_admin=True).exclude(user=user).select_related('user')
        self.stdout.write(self.style.SUCCESS(f"{user.email} is now the Head Admin."))
        if others:
            self.stdout.write(self.style.WARNING(
                "Other Head Admins: " + ", ".join(p.user.email for p in others)
                + ". Remove them with --remove if there should be only one."))
