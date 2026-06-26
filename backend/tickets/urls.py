from django.urls import path

from . import views

urlpatterns = [
    path("health", views.HealthView.as_view(), name="health"),
    path("analyze-ticket", views.AnalyzeTicketView.as_view(), name="analyze-ticket"),
]