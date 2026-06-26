from django.urls import path

from . import views

urlpatterns = [
    path("health", views.HealthView.as_view(), name="health"),
    path("summarize", views.SummarizeView.as_view(), name="summarize"),
    path("analyze-ticket", views.AnalyzeTicketView.as_view(), name="analyze-ticket"),
]