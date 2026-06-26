"""Custom DRF exception handler.

DRF returns 400 for validation errors by default; the original FastAPI/Pydantic
contract returned 422. Preserve that so existing callers/tests keep working.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and response.status_code == status.HTTP_400_BAD_REQUEST:
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return response