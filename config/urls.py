"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
from django.contrib.auth import views as auth_views
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("cadastros/", include("apps.cadastros.urls")),
    path("pregoes/", include("apps.pregoes.urls")),
    path("execucao/", include("apps.execucao.urls")),
    path("documentos/", include("apps.documentos.urls")),
    path("usuarios/", include("apps.usuarios.urls")),
    path("importacoes/", include("apps.importacoes.urls")),
    path("auditoria/", include("apps.auditoria.urls")),
    path("aquisicoes/", include("apps.aquisicoes.urls")),
]