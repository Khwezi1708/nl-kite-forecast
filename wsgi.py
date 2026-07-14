"""WSGI entrypoint for production (gunicorn on Render)."""

from app import app, bootstrap_services

bootstrap_services()
