from django.urls import path
from . import views

urlpatterns = [
    path('',                        views.dashboard,      name='dashboard'),
    path('submit/',                 views.submit_review,  name='submit_review'),
    path('api/status/<int:pk>/',    views.review_status,  name='review_status'),
]
