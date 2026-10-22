from django.urls import path
from django.contrib.auth import views as auth_views
from user.decorators import admin_required, head_admin_required
from . import views
from . import user_management
from . import user_import
from . import personnel_management

urlpatterns = [
    path('dashboard/', admin_required(views.dashboard), name='dashboard'),
    path('reports/', admin_required(views.reports), name='reports'),
    path('reports/<int:report_id>/', admin_required(views.report_detail), name='adm_report_detail'),
    path('reports/<int:report_id>/job-order/pdf/', admin_required(views.export_job_order_pdf_admin), name='exportJobOrderPDFAdmin'),
    path('reports/<int:report_id>/save/', admin_required(views.report_save), name='adm_report_save'),
    path('reports/<int:report_id>/personnel/', admin_required(views.save_report_personnel), name='adm_report_personnel'),
    path('reports/<int:report_id>/status/', admin_required(views.report_status), name='adm_report_status'),
    path('reports/<int:report_id>/manual-survey/', admin_required(views.record_manual_survey), name='adm_manual_survey'),
    path('reports/<int:report_id>/personnel-rating/', head_admin_required(views.record_med_personnel_rating), name='adm_personnel_rating'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logoutadmin'),
    path('menu/', admin_required(views.ProfileMenu), name='ProfileMenu'),
    path('ajax/menu/', admin_required(views.adm_menu_ajax), name='adm_menu_ajax'),
    path('rating-analytics/', head_admin_required(views.rating_analytics), name='rating_analytics'),
    path('ajax/rating-analytics/', head_admin_required(views.adm_rating_analytics_ajax), name='adm_rating_analytics_ajax'),
    path('ajax/rating-analytics/responses/', head_admin_required(views.adm_rating_responses_ajax), name='adm_rating_responses_ajax'),
    path('rating-analytics/pdf/', head_admin_required(views.export_personnel_performance_pdf), name='export_personnel_performance_pdf'),
    path('notifications/', admin_required(views.adminNotifications), name='adminNotifications'),
    path('ajax/notifications/', admin_required(views.get_notifications_ajax_admin), name='get_notifications_ajax_admin'),
    path('ajax/notifications-page/', admin_required(views.get_notifications_page_ajax_admin), name='get_notifications_page_ajax_admin'),
    path('ajax/notifications/mark-all-read/', admin_required(views.mark_all_notifications_read_admin), name='mark_all_notifications_read_admin'),
    path('notifications/<int:notification_id>/view/', admin_required(views.view_notification_admin), name='view_notification_admin'),
    path('ajax/reports/', admin_required(views.adm_reports_ajax), name='adm_reports_ajax'),
    path('ajax/dashboard/', admin_required(views.adm_dashboard_ajax), name='adm_dashboard_ajax'),
    path('dashboard/report/pdf/', head_admin_required(views.export_dashboard_report_pdf), name='export_dashboard_report_pdf'),
    path('dashboard/report/summary/', head_admin_required(views.dashboard_report_summary), name='dashboard_report_summary'),

    # User Management
    path('ajax/personnels/', head_admin_required(personnel_management.personnel_list), name='personnel_management_ajax'),
    path('personnels/add/', head_admin_required(personnel_management.personnel_save), name='personnel_add'),
    path('personnels/<int:personnel_id>/edit/', head_admin_required(personnel_management.personnel_save), name='personnel_edit'),
    path('personnels/<int:personnel_id>/delete/', head_admin_required(personnel_management.personnel_delete), name='personnel_delete'),
    path('users/', head_admin_required(user_management.user_management), name='user_management'),
    path('ajax/users/', head_admin_required(user_management.user_management_ajax), name='user_management_ajax'),
    path('users/<int:user_id>/action/', head_admin_required(user_management.user_management_action), name='user_management_action'),
    path('users/import/template/', head_admin_required(user_import.import_template), name='user_import_template'),
    path('users/import/preview/', head_admin_required(user_import.import_preview), name='user_import_preview'),
    path('users/import/confirm/', head_admin_required(user_import.import_confirm), name='user_import_confirm'),

    

]
