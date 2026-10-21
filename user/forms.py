import os
from django import forms
from django.contrib.auth.models import User
from .models import UserProfile
from .models import Report
from adm.models import Building, Floor, Room
from .validators import validate_no_gibberish
from .validators import validate_no_profanity
from .validators import validate_name  # name sanitizer
from .validators import validate_office_name
from .validators import validate_password_strength


class ProfilePasswordChangeForm(forms.Form):
    old_password = forms.CharField(required=False, widget=forms.PasswordInput)
    new_password1 = forms.CharField(required=False, widget=forms.PasswordInput)
    new_password2 = forms.CharField(required=False, widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        old = data.get('old_password', '')
        new = data.get('new_password1', '')
        confirmation = data.get('new_password2', '')
        if not old and not new and not confirmation:
            raise forms.ValidationError('Please fill in the required fields.')
        if not old:
            raise forms.ValidationError('Please enter your current password.')
        if not new or not confirmation:
            raise forms.ValidationError('Please fill in both new password fields.')
        if not self.user.check_password(old):
            raise forms.ValidationError('Old password is incorrect.')
        if new != confirmation:
            raise forms.ValidationError('New passwords do not match.')
        if new == old:
            raise forms.ValidationError('New Password must be different from your current password.')
        errors = validate_password_strength(new)
        if errors:
            raise forms.ValidationError(errors)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save()
        return self.user


def form_error_text(*forms_to_check):
    return ' '.join(
        str(error)
        for form in forms_to_check
        for errors in form.errors.values()
        for error in errors
    )

class ReportForm(forms.ModelForm):
    building = forms.ModelChoiceField(queryset=Building.objects.all(), empty_label="Select a Building or Facility", required=True)
    floor = forms.ModelChoiceField(queryset=Floor.objects.none(), empty_label="Select a Floor", required=True)
    location = forms.ModelChoiceField(queryset=Room.objects.none(), empty_label="Select a Room or an Area", required=True)
    photo = forms.ImageField(required=True)

    class Meta:
        model = Report
        fields = [
            'photo', 'category', 'custom_category', 'location',
            'inventory_tag_no', 'property_inventory_no', 'description',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        tailwind_classes = 'w-full appearance-none border border-gray-200 rounded-xl px-3 py-2 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent cursor-pointer pr-10'
        self.fields['building'].widget.attrs.update({'class': tailwind_classes, 'id': 'id_building'})
        self.fields['floor'].widget.attrs.update({'class': tailwind_classes, 'id': 'id_floor'})
        self.fields['location'].widget.attrs.update({'class': tailwind_classes, 'id': 'id_location'})
        self.fields['category'].widget.attrs.update({'class': tailwind_classes, 'id': 'id_category'})
        self.fields['custom_category'].widget.attrs.update({'class': 'w-full border border-gray-200 rounded-xl px-3 py-2 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent', 'id': 'id_custom_category'})
        inventory_input_classes = 'w-full border border-gray-200 rounded-xl px-3 py-2 text-sm text-gray-700 placeholder-gray-400 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        for name, placeholder in (
            ('inventory_tag_no', 'e.g. D-ICS-2026-03-26'),
            ('property_inventory_no', 'e.g. UDM-D-ICS-2026-24'),
        ):
            self.fields[name].widget.attrs.update({'class': inventory_input_classes, 'placeholder': placeholder})

        # Custom dropdown (cdd) para sa apat na select
        for name in ('building', 'floor', 'location', 'category'):
            self.fields[name].widget.attrs.update({'data-cdd': ''})

        #For Catching Errors and Validationzzz
        if 'building' in self.data:
            try:
                building_id = int(self.data.get('building'))
                self.fields['floor'].queryset = Floor.objects.filter(building_id=building_id).order_by('floor_number')
            except (ValueError, TypeError):
                pass
        
        if 'floor' in self.data:
            try:
                floor_id = int(self.data.get('floor'))
                self.fields['location'].queryset=Room.objects.filter(floor_id=floor_id).order_by('order')
            except (ValueError, TypeError):
                pass

    def clean_custom_category(self):
        value = (self.cleaned_data.get('custom_category') or '').strip()
        if value:
            if len(value) > 30:
                raise forms.ValidationError("Custom category must not exceed 30 characters.")
            if validate_no_profanity(value) or validate_no_gibberish(value):
                raise forms.ValidationError("Invalid input. Nonsense or foul words are not accepted.")
        return value

    def clean_description(self):
        value = self.cleaned_data.get('description', '').strip()
        if len(value) > 300:
            raise forms.ValidationError("Description must not exceed 300 characters.")
        if validate_no_profanity(value) or validate_no_gibberish(value):
            raise forms.ValidationError("Invalid input. Nonsense or foul words are not accepted.")
        return value


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        # Email is not editable here: it is the sign-in name and where reset codes go, so
        # changing it without re-verification would let anyone at an open session take the account.
        fields = ['first_name', 'last_name']

    # Sanitizes first/last name (shared by admin & user)
    def clean_first_name(self):
        return validate_name(self.cleaned_data.get('first_name'), "First name")
    
    def clean_last_name(self):
        return validate_name(self.cleaned_data.get('last_name'), "Last name")

class UserProfileForm(forms.ModelForm):
    # Only used when "Other Office or Area" is picked; the typed text replaces the department
    other_office = forms.CharField(required=False, max_length=100)

    class Meta:
        model = UserProfile
        fields = ['department', 'phone']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Students and faculty pick a college, staff pick an office or area. A value saved
        # before this rule existed stays selectable, so editing only the phone number
        # never forces a department change.
        profile = self.instance
        choices = list(UserProfile.department_choices_for(profile.role))
        if profile.department and profile.department not in dict(choices):
            choices.append((profile.department, dict(UserProfile.DEPARTMENT_CHOICES + UserProfile.OFFICE_CHOICES).get(profile.department, profile.department)))
        self.fields['department'].choices = [('', '---------'), *choices]

    def _get_validation_exclusions(self):
        # The offices are not in the model field's choices (that would need a migration),
        # so department is checked by this form's own list above instead.
        return set(super()._get_validation_exclusions()) | {'department'}

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('department') == UserProfile.OTHER_OFFICE_CODE:
            office, office_errors = validate_office_name(cleaned_data.get('other_office'))
            for error in office_errors:
                self.add_error('other_office', error)
            if not office_errors:
                cleaned_data['department'] = office
        return cleaned_data

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone:
            if not phone.isdigit():
                raise forms.ValidationError("Phone number must contain numbers only.")
            if len(phone) !=11:
                raise forms.ValidationError("Phone number must be exactly 11 digits.")
        return phone

class ProfilePhotoForm(forms.ModelForm):
    ALLOWED_EXTENSIONS = ['.jpg', '.jpeg', '.png']

    class Meta:
        model = UserProfile
        fields = ['profile_photo']

    def clean_profile_photo(self):
        photo = self.cleaned_data.get('profile_photo')
        if photo:
            ext = os.path.splitext(photo.name)[1].lower()
            if ext not in self.ALLOWED_EXTENSIONS:
                raise forms.ValidationError("Unsupported file type. Please upload a JPG or PNG image.")
            
            if photo.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Profile photo size should not exceed 5MB.")
        return photo
