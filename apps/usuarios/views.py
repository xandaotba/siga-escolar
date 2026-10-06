from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AlterarSenhaUsuarioForm, MinhaContaForm, UsuarioForm
from .models import PerfilUsuario


def usuario_tem_acesso_usuarios(user):
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    perfil = getattr(user, "perfil_acesso", None)

    return bool(
        perfil
        and perfil.perfil == PerfilUsuario.PERFIL_ADMINISTRADOR
        and user.is_active
    )



@login_required
def minha_conta(request):
    """
    Permite que qualquer usuário autenticado altere os próprios dados.

    Administrador:
    - usa o formulário administrativo completo;
    - pode alterar Perfil, Escola vinculada e status.

    Demais perfis:
    - podem alterar somente os próprios dados básicos, login e senha;
    - Perfil, Escola vinculada e status não fazem parte do formulário POST.
    """
    usuario = request.user
    pode_editar_perfil = usuario_tem_acesso_usuarios(usuario)
    perfil_atual = getattr(usuario, "perfil_acesso", None)

    if pode_editar_perfil:
        FormClass = UsuarioForm
    else:
        FormClass = MinhaContaForm

    if request.method == "POST":
        form = FormClass(
            request.POST,
            instance=usuario,
        )

        if form.is_valid():
            senha_alterada = bool(
                form.cleaned_data.get("password1")
            )

            usuario_salvo = form.save()

            # Quando o próprio usuário troca a senha, mantém a sessão atual
            # autenticada usando o novo hash de autenticação.
            if senha_alterada:
                update_session_auth_hash(
                    request,
                    usuario_salvo,
                )

            messages.success(
                request,
                "Seus dados foram atualizados com sucesso.",
            )
            return redirect("usuarios:minha_conta")
    else:
        form = FormClass(instance=usuario)

    return render(
        request,
        "usuarios/minha_conta.html",
        {
            "form": form,
            "usuario_editado": usuario,
            "perfil_atual": perfil_atual,
            "pode_editar_perfil": pode_editar_perfil,
            "titulo": "Minha Conta",
            "botao": "Salvar Alterações",
        },
    )


@login_required
@user_passes_test(usuario_tem_acesso_usuarios, login_url="dashboard")
def usuarios(request):
    usuarios_lista = (
        User.objects.all()
        .select_related("perfil_acesso", "perfil_acesso__escola")
        .order_by("first_name", "username")
    )

    total_usuarios = usuarios_lista.count()
    total_ativos = usuarios_lista.filter(is_active=True).count()
    total_inativos = usuarios_lista.filter(is_active=False).count()

    return render(
        request,
        "usuarios/usuarios.html",
        {
            "usuarios": usuarios_lista,
            "total_usuarios": total_usuarios,
            "total_ativos": total_ativos,
            "total_inativos": total_inativos,
        },
    )


@login_required
@user_passes_test(usuario_tem_acesso_usuarios, login_url="dashboard")
def novo_usuario(request):
    if request.method == "POST":
        form = UsuarioForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Usuário cadastrado com sucesso.")
            return redirect("usuarios:usuarios")
    else:
        form = UsuarioForm()

    return render(
        request,
        "usuarios/usuario_form.html",
        {
            "form": form,
            "titulo": "Novo Usuário",
            "botao": "Cadastrar Usuário",
        },
    )


@login_required
@user_passes_test(usuario_tem_acesso_usuarios, login_url="dashboard")
def editar_usuario(request, usuario_id):
    usuario = get_object_or_404(User, id=usuario_id)

    if request.method == "POST":
        form = UsuarioForm(request.POST, instance=usuario)

        if form.is_valid():
            form.save()
            messages.success(request, "Usuário atualizado com sucesso.")
            return redirect("usuarios:usuarios")
    else:
        form = UsuarioForm(instance=usuario)

    return render(
        request,
        "usuarios/usuario_form.html",
        {
            "form": form,
            "usuario_editado": usuario,
            "titulo": "Editar Usuário",
            "botao": "Salvar Alterações",
        },
    )


@login_required
@user_passes_test(usuario_tem_acesso_usuarios, login_url="dashboard")
def alterar_senha_usuario(request, usuario_id):
    usuario = get_object_or_404(User, id=usuario_id)

    if request.method == "POST":
        form = AlterarSenhaUsuarioForm(request.POST, usuario=usuario)

        if form.is_valid():
            form.save()
            messages.success(request, "Senha alterada com sucesso.")
            return redirect("usuarios:usuarios")
    else:
        form = AlterarSenhaUsuarioForm(usuario=usuario)

    return render(
        request,
        "usuarios/usuario_senha.html",
        {
            "form": form,
            "usuario_editado": usuario,
        },
    )


@login_required
@user_passes_test(usuario_tem_acesso_usuarios, login_url="dashboard")
def alternar_status_usuario(request, usuario_id):
    usuario = get_object_or_404(User, id=usuario_id)

    if request.method != "POST":
        return redirect("usuarios:usuarios")

    if usuario == request.user:
        messages.error(request, "Você não pode inativar o próprio usuário logado.")
        return redirect("usuarios:usuarios")

    if usuario.is_superuser:
        messages.error(request, "O superusuário principal não pode ser inativado por esta tela.")
        return redirect("usuarios:usuarios")

    usuario.is_active = not usuario.is_active
    usuario.save(update_fields=["is_active"])

    if usuario.is_active:
        messages.success(request, "Usuário ativado com sucesso.")
    else:
        messages.success(request, "Usuário inativado com sucesso.")

    return redirect("usuarios:usuarios")
