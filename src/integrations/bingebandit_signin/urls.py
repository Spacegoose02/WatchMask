from django.urls import path

from integrations.bingebandit_signin import views

urlpatterns = [
    path("bingebandit/login/", views.login, name="bingebandit_login"),
    path("bingebandit/wait/", views.wait, name="bingebandit_wait"),
    path("bingebandit/poll/", views.poll, name="bingebandit_poll"),
    path("bingebandit/login/callback/", views.callback, name="bingebandit_callback"),
]
