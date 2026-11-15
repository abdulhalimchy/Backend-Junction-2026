from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Authentication
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    # Current user
    path("me/", views.MeView.as_view(), name="me"),
    path("me/profile/", views.MyProfileView.as_view(), name="me-profile"),
    path("me/change-password/", views.ChangePasswordView.as_view(), name="change-password"),
    path("me/delete/", views.DeleteAccountView.as_view(), name="delete-account"),
]
