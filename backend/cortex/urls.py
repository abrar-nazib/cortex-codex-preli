"""Project URL routing.

  GET  /health        — service health
  POST /sort-ticket   — classify one ticket
  GET  /docs/          — drf_spectacular swagger UI   (gated by ENABLE_DOCS)
  GET  /api/schema/    — OpenAPI schema               (gated by ENABLE_DOCS)

The docs surface is mounted only when settings.ENABLE_DOCS is true
(DJANGO_ENABLE_DOCS env). Prod flips it off via docker-compose.prod.yml;
when off, both /docs/ and /api/schema/ 404.
"""
from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path("", include("tickets.urls")),
]

if settings.ENABLE_DOCS:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("docs/", SpectacularSwaggerView.as_view(url="/api/schema/"), name="swagger-ui"),
    ]