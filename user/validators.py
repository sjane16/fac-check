import re
from django.core.validators import RegexValidator 
from django.core.exceptions import ValidationError

def validate_no_gibberish(value):
    """Returns a list of error messages. Empty list = looks like real text.
    Flags runs of 5+ consecutive consonants with no vowel in between,
    which is characteristic of random keyboard-mashing (e.g. "dhasjdjasdja")."""
    errors = []
    if re.search(r'[b-df-hj-np-tv-z]{5,}', value, re.IGNORECASE):
        errors.append("Please enter a valid, descriptive input — random or nonsensical text is not allowed.")
    return errors


# Filipino/English foul-word filter. Word-boundary + char-substitution aware
# (so "put4ng1na", "TANGINA", "ta-nga" etc. still get caught).
_PROFANITY_PATTERNS = [
    r'f+u*c+k+',
    r'p+u*t+[a@4]*n+g+[i1!]*n+[a@4]*',       # putangina
    r'p+u*t+[a@4]*n+g+',                     # putang (ina)
    r't+[a@4]*n+g+[i1!]*n+[a@4]*',           # tangina
    r'g+[a@4]*g+[a@4o0]*',                   # gago / gaga
    r't+[a@4]*r+[a@4]*n+t+[a@4]*d+[a@4o0]*', # tarantado
    r't+[a@4]*n+g+[a@4]*',                   # tanga
]
_PROFANITY_RE = re.compile(
    r'\b(?:' + '|'.join(_PROFANITY_PATTERNS) + r')\b',
    re.IGNORECASE
)


def validate_no_profanity(value):
    """Returns a list of error messages. Empty list = no foul words detected.
    Catches common Filipino/English curse words, including basic letter
    substitutions (e.g. 4->a, 1/!->i, 0->o) and repeated letters."""
    errors = []
    stripped = re.sub(r'[\s\-_.]+', '', value)
    if _PROFANITY_RE.search(value) or _PROFANITY_RE.search(stripped):
        errors.append("Invalid input. Nonsense or foul words are not accepted.")
    return errors


def validate_password_strength(password):
    """Returns a list of error messages. Empty list = valid password."""
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if re.search(r'\s', password):
        errors.append("Password must not contain spaces or other whitespace.")
    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter.")
    if not re.search(r'[0-9]', password):
        errors.append("Password must contain at least one number.")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=~`\[\]\\/;\']', password):
        errors.append("Password must contain at least one symbol.")
    return errors

# Staff who pick "Other Office or Area" type their own office. Same checks everywhere it is typed.
OFFICE_NAME_MAX_LENGTH = 100
OFFICE_NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿÑñ0-9.,'&()\- ]+$")

def validate_office_name(value):
    """Returns (cleaned_value, errors). Empty errors list = OK to save."""
    value = re.sub(r'\s+', ' ', value or '').strip()
    errors = []
    if not value:
        errors.append("Please type your office or area.")
    elif len(value) > OFFICE_NAME_MAX_LENGTH:
        errors.append(f"Office or area must not exceed {OFFICE_NAME_MAX_LENGTH} characters.")
    elif not re.search(r'[A-Za-zÀ-ÖØ-öø-ÿÑñ]{2}', value):
        errors.append("Please enter a valid office or area name.")
    elif not OFFICE_NAME_PATTERN.match(value):
        errors.append("Office or area can only contain letters, numbers, spaces and . , ' & ( ) -")
    else:
        errors.extend(validate_no_gibberish(value))
        errors.extend(validate_no_profanity(value))
    return value, errors

phone_validator = RegexValidator (
    regex=r'^\d{11}$',
    message="Phone number must be exactly 11 digits, numbers only"
)

# Sanitizes first/last name input
NAME_MAX_LENGTH = 50
NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿÑñ.'\- ]+$")

def validate_name(value, field_label = "Name"):
    """Tutulungan tayo magsanitize nito, guys <3"""

    value = (value or '').strip()

    if not value:
        raise ValidationError(f"{field_label} is required.")
    
    if len(value) > NAME_MAX_LENGTH:
        raise ValidationError(f"{field_label} must not exceed {NAME_MAX_LENGTH} characters.")

    # Blocks numbers/symbols
    if not NAME_PATTERN.match(value):     
        raise ValidationError(
            f"{field_label} can only contain letters, spaces, hyphens ( - ), apostrophes ( ' ), and periods ( . )."
        )
    
    return value
