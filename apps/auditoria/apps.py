from django.apps import AppConfig


class AuditoriaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auditoria"
    verbose_name = "Auditoria"

    def ready(self):
        # Registra sinais de login/logout.
        from . import signals  # noqa: F401
