from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_redirect, name='home'),  # Main landing page redirects to login
    path('administrator/', views.decoy_admin, name='decoy_admin'),
]