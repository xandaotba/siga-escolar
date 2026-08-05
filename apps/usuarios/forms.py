from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from apps.cadastros.models import Escola

from .models import PerfilUsuario


class UsuarioForm(forms.Form):
    first_name = forms.CharField(
        label="Nome",
        max_length=150,
        required=True,
    )
    last_name = forms.CharField(
        label="Sobrenome",
        max_length=150,
        required=False,
    )
    username = forms.CharField(
        label="Usuário",
        max_length=150,
        required=True,
    )
    email = forms.EmailField(
        label="E-mail",
        required=False,
    )
    perfil = forms.ChoiceField(
        label="Perfil",
        choices=PerfilUsuario.PERFIL_CHOICES,
        required=True,
    )
    escola = forms.ModelChoiceField(
        label="Escola vinculada",
        queryset=Escola.objects.none(),
        required=False,
        help_text="Obrigatório apenas se quiser restringir futuramente o perfil Consulta/Escola a uma escola específica.",
    )
    is_active = forms.BooleanField(
        label="Usuário ativo",
        required=False,
        initial=True,
    )
    password1 = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput,
        required=False,
    )
    password2 = forms.CharField(
        label="Confirmar senha",
        widget=forms.PasswordInput,
        required=False,
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["escola"].queryset = Escola.objects.all().order_by("nome_escola")

        if instance:
            perfil = getattr(instance, "perfil_acesso", None)
            self.fields["first_name"].initial = instance.first_name
            self.fields["last_name"].initial = instance.last_name
            self.fields["username"].initial = instance.username
            self.fields["email"].initial = instance.email
            self.fields["is_active"].initial = instance.is_active

            if perfil:
                self.fields["perfil"].initial = perfil.perfil
                self.fields["escola"].initial = perfil.escola

            self.fields["password1"].required = False
            self.fields["password2"].required = False
            self.fields["password1"].help_text = "Preencha apenas se quiser alterar a senha."
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True

    def clean_username(self):
        username = self.cleaned_data["username"].strip()

        qs = User.objects.filter(username__iexact=username)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)

        if qs.exists():
            raise forms.ValidationError("Já existe um usuário com este nome de usuário.")

        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")

        if password1 or password2:
            if password1 != password2:
                raise forms.ValidationError("As senhas informadas não conferem.")

            validate_password(password1, self.instance)

        return cleaned

    def save(self):
        if self.instance:
            user = self.instance
        else:
            user = User()

        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.username = self.cleaned_data["username"]
        user.email = self.cleaned_data["email"]
        user.is_active = self.cleaned_data["is_active"]

        perfil = self.cleaned_data["perfil"]

        # Administrador também fica com acesso ao admin do Django.
        if perfil == PerfilUsuario.PERFIL_ADMINISTRADOR:
            user.is_staff = True
        elif not user.is_superuser:
            user.is_staff = False

        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)

        user.save()

        perfil_usuario, _ = PerfilUsuario.objects.get_or_create(usuario=user)
        perfil_usuario.perfil = perfil
        perfil_usuario.escola = self.cleaned_data.get("escola")
        perfil_usuario.save()

        return user


class AlterarSenhaUsuarioForm(forms.Form):
    password1 = forms.CharField(
        label="Nova senha",
        widget=forms.PasswordInput,
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar nova senha",
        widget=forms.PasswordInput,
        required=True,
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")

        if password1 != password2:
            raise forms.ValidationError("As senhas informadas não conferem.")

        validate_password(password1, self.usuario)

        return cleaned

    def save(self):
        self.usuario.set_password(self.cleaned_data["password1"])
        self.usuario.save()
        return self.usuario
