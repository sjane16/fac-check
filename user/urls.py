from django.urls import path
from django.contrib.auth import views as auth_views
from .decorators import user_required
from . import views

urlpatterns = [
    path('user_dashboard/', user_required(views.userDashboard), name='userDashboard'),
    path('menu/', user_required(views.menu), name='menu'),
    path('ajax/menu/', user_required(views.menuAjax), name= 'menuAjax'),
    path('survey/submit/<int:report_id>/', user_required(views.submitSurvey), name='submitSurvey'),
    path('survey/method/<int:report_id>/', user_required(views.chooseSurveyMethod), name='chooseSurveyMethod'),
    path('survey/', user_required(views.userSurvey), name='userSurvey'),
    path('report_form/', user_required(views.reportForm), name='reportForm'),
    path('user_reports/', user_required(views.userReports), name='userReports'),
    path('reported_issue/<int:report_id>/', user_required(views.reportedIssue), name='reportedIssue'),
    path('job-order/<int:report_id>/pdf/', user_required(views.exportJobOrderPDF), name='exportJobOrderPDF'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('chatbot/response/', user_required(views.chatbot_response), name='chatbot_response'),
    path('ajax/load-floors/', user_required(views.load_floors), name='ajax_load_floors'),
    path('ajax/load-rooms/', user_required(views.load_rooms), name='ajax_load_rooms'),
    path('ajax/predict-category/', user_required(views.predictCategory), name='predictCategory'),
    path('notifications/', user_required(views.userNotifications), name='userNotifications'),
    path('ajax/notifications/', user_required(views.get_notifications_ajax), name='get_notifications_ajax'),
    path('ajax/notifications-page/', user_required(views.get_notifications_page_ajax), name='get_notifications_page_ajax'),
    path('ajax/notifications/mark-all-read/', user_required(views.mark_all_notifications_read), name='mark_all_notifications_read'),
    path('notifications/<int:notification_id>/view/', user_required(views.view_notification), name='view_notification'),
    path('ajax/user-reports/', user_required(views.userReportsAjax), name='userReportsAjax'),
    path('ajax/dashboard-stats/', user_required(views.dashboardStatsAjax), name='dashboardStatsAjax'),
    path('ajax/report-form/', user_required(views.reportFormAjax), name='reportFormAjax'),
    path('ajax/survey/', user_required(views.userSurveyAjax), name='userSurveyAjax'),




]
