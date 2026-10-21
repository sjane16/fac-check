from django.urls import path
from . import views
from . import passkeys
from django.conf import settings


urlpatterns = [
    path('index/', views.splash_screen, name='index'),
    path('contact/', views.contact, name='contact'),
    path('register/',views.signup, name='signup'),
    path('login/', views.login_view, name='login'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('resend-otp/', views.resend_otp, name='resend_otp'),
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('verify-login-otp/', views.verify_login_otp, name='verify_login_otp'),
    path('resend-login-otp/', views.resend_login_otp, name='resend_login_otp'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('verify-reset-otp/', views.verify_reset_otp, name='verify_reset_otp'),
    path('resend-reset-otp/', views.resend_reset_otp, name='resend_reset_otp'),
    path('reset-password/', views.reset_password, name='reset_password'),
    path('set-password/', views.force_password_change, name='force_password_change'),

    # Passkeys (fingerprint / face / screen-lock sign-in)
    path('passkeys/register/options/', passkeys.register_options, name='passkey_register_options'),
    path('passkeys/register/verify/', passkeys.register_verify, name='passkey_register_verify'),
    path('passkeys/login/options/', passkeys.login_options, name='passkey_login_options'),
    path('passkeys/login/verify/', passkeys.login_verify, name='passkey_login_verify'),
    path('passkeys/<int:passkey_id>/rename/', passkeys.rename, name='passkey_rename'),
    path('passkeys/<int:passkey_id>/remove/', passkeys.remove, name='passkey_remove'),
]

