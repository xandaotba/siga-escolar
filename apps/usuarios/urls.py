from django.urls import path

from . import views

app_name = "usuarios"

urlpatterns = [
    path("", views.usuarios, name="usuarios"),
    path("novo/", views.novo_usuario, name="novo_usuario"),
    path("<int:usuario_id>/editar/", views.editar_usuario, name="editar_usuario"),
    path("<int:usuario_id>/senha/", views.alterar_senha_usuario, name="alterar_senha_usuario"),
    path("<int:usuario_id>/status/", views.alternar_status_usuario, name="alternar_status_usuario"),
]
