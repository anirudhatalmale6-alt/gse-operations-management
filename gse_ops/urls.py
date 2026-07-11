from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from ops import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("equipment/", views.equipment_list, name="equipment_list"),
    path("equipment/new/", views.equipment_create, name="equipment_create"),
    path("equipment/export/", views.equipment_export, name="equipment_export"),
    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
    path("equipment/<int:pk>/edit/", views.equipment_edit, name="equipment_edit"),
    path("equipment/<int:pk>/move/", views.movement_create, name="movement_create"),

    path("movements/", views.movement_list, name="movement_list"),
    path(
        "movements/<int:pk>/<str:action>/",
        views.movement_action,
        name="movement_action",
    ),

    path("locations/", views.location_list, name="location_list"),
    path("locations/new/", views.location_create, name="location_create"),
    path("locations/<int:pk>/", views.location_detail, name="location_detail"),
    path("locations/<int:pk>/edit/", views.location_edit, name="location_edit"),

    path("employees/", views.employee_list, name="employee_list"),
    path("employees/new/", views.employee_create, name="employee_create"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),

    path("departments/", views.department_list, name="department_list"),
    path("compliance/", views.compliance, name="compliance"),
    path("oos/", views.oos_register, name="oos_register"),
    path("oos/<int:pk>/close/", views.oos_close, name="oos_close"),
    path("daily-report/", views.daily_report, name="daily_report"),
    path(
        "daily-report/export/",
        views.daily_report_export,
        name="daily_report_export",
    ),

    path(
        "login/",
        auth_views.LoginView.as_view(template_name="ops/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
